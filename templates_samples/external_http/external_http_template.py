try:
    from external_listener import ExternalListener, ExternalListenerCommands
except:
    print("[INFO] external_listener.py file not here, looking from ../../external_listener_lib directory")
    import sys
    sys.path.append('../../external_listener_lib')
    from external_listener import ExternalListener, ExternalListenerCommands
    print("[INFO]   Jep, there it is")
from http.server import BaseHTTPRequestHandler, HTTPServer
import time 
import json
import sys

list_of_commands = {
    "my_sleep": 
    {
        "description": "Change connection sleep time",
        "parameters":
        {
            "sleep": { "default": 1, "required": True, "type": "string" }
        },
        "agent_types": ["python", "dotNET"]
    },
    "my_terminal": 
    {
        "description": "Run terminal command",
        "parameters":
        {
            "command": { "default": "whoami", "required": True, "type": "string" }
        },
        "agent_types": ["python", "dotNET"]
    },
    "my_what": 
    {
        "description": "Give info about the agent",
        "parameters": {},
        "agent_types": ["python", "dotNET"]
    },
    "my_eval": 
    {
        "description": "Eval command inside agent",
        "parameters":
        {
            "code": { "default": "print('TEST')", "required": True, "type": "string" }
        },
        "agent_types": ["python"]
    }
}

class Command:
    def __init__(self, cmd_id, cmd_type, cmd_conf = None):
        self.id = cmd_id
        self.type = cmd_type
        self.conf = cmd_conf
        
    def getDict(self):
        result = {}
        if self.conf is not None:
            result = self.conf
        result["__type__"] = self.type
        return result

class Agent:
    def __init__(self, guid, ext):
        self.ext = ext
        self.guid = guid
        self.commands = []
        self.currentCommandId = None
        
    def addCommand(self, cmd_id, cmd_type, cmd_conf = None):
        self.commands.append(Command(cmd_id, cmd_type, cmd_conf))
        
    def getNextCommand(self):
        if len(self.commands) == 0:
            return None
        new_cmd = self.commands[0]
        self.commands = self.commands[1:]
        self.currentCommandId = new_cmd.id
        self.ext.command_sent(new_cmd.id)
        return new_cmd
        
    def addResultTxt(self, success, result, command_id = None):
        if command_id is None:
            command_id = self.currentCommandId
        self.ext.new_result(self.guid,  command_id, success, result_txt = {"STDOUT": result})
        
    def addResultFailed(self, error, command_id = None):
        if command_id is None:
            command_id = self.currentCommandId
        self.ext.new_result(self.guid,  command_id, False, error_msg = error)
        

class Controller:
    def __init__(self):
        self.agents = {}
        self.ext = ExternalListener()
        
    def connect(self, host, port):
        self.ext.connect(host, port, on_command = self.new_command_arrived, on_connect =self.when_connected)  
    
        
    def new_command_arrived(self, guid, cmd_type, command_id, conf):
        global list_of_commands
        try:
            if cmd_type not in list_of_commands:
                self.ext.new_result(guid,  command_id, False, {"STDOUT": "Unknown command '" + cmd_type + "'"}, error_msg = "Unknown command '" + cmd_type + "'")
            cmd_setup = list_of_commands[cmd_type]
            cmd_relay_conf = {}
            for param_name in cmd_setup["parameters"]:
                if param_name in conf:
                    cmd_relay_conf[param_name] = conf[param_name]                
            self.agents[guid].addCommand(command_id, cmd_type, cmd_relay_conf)
        except Exception as e:
            raise
            self.ext.new_result(guid,  command_id, False, {"STDOUT": str(e)}, error_msg = str(e))

    def register_agent_commands(self, agent_id, agent_type):
        global list_of_commands
        commands = ExternalListenerCommands()
        for command_name in list_of_commands:
            command = list_of_commands[command_name]
            if agent_type in command["agent_types"]:
                commands.add_command_simple(command_name, command["description"], command["parameters"])
        self.ext.register_commands(commands, agent_id)  
            
    def addAgent(self, guid, metadata, agent_type):
        if guid in self.agents:
            return
        print("Agent %s connected" % guid)
        self.ext.new_agent(guid, metadata = metadata)
        self.agents[guid] = Agent(guid, self.ext)
        self.register_agent_commands(guid, agent_type)
        
    def get_agent(self, guid):
        if guid not in self.agents:
            return None
        return self.agents[guid]
        
    def when_connected(self):
        pass
        

class MyServer(BaseHTTPRequestHandler):
    def do_POST(self):
        data = self.rfile.read(int(self.headers['Content-Length']))
        data = json.loads(data)
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        if "id" not in data:
            self.wfile.write(bytes("{}", "utf-8"))
            return
                        
        global controller            
        guid = data["id"]
        agent_type = data["type"]
        metadata = {}
        if "username" in data:
            metadata["username"] = data["username"]
        if "hostname" in data:
            metadata["hostname"] = data["hostname"]
        controller.addAgent(guid, metadata, agent_type)
            
        if "result" in data:
            result = data["result"]
            print("Got result: %d characters" % len(result))
            controller.get_agent(guid).addResultTxt(True, result)            
            
        command = controller.get_agent(guid).getNextCommand()
        if command is None:
            self.wfile.write(bytes("{}", "utf-8"))
            return
            
        self.wfile.write(bytes(json.dumps(command.getDict()), "utf-8"))
        

if len(sys.argv) != 5:
    print("Wrong syntax!")
    print("%s <external listener hostname/IP> <external listener port> <HTTP interface for listening> <HTTP port>" % sys.argv[0])
    exit()
    
controller = Controller()
controller.connect(sys.argv[1], int(sys.argv[2]))
myServer = HTTPServer((sys.argv[3], int(sys.argv[4])), MyServer)
myServer.serve_forever()