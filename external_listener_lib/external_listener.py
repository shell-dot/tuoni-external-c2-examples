import websocket
import json
import threading
import time 
import base64

class ExternalListener:
    def __init__(self, verbose = False):
        self.main_thread = None
        self.ws = None
        self.on_command = None
        self.on_connect = None
        self.verbose = verbose
        
    def print_info(self, msg):
        if self.verbose:
            print(msg)

    def on_message(self, ws, message):
        self.print_info("[MESSAGE BY C2]\n" + message + "\n\n")
        data = json.loads(message)
        if data["type"] == "start-command":
            guid = data["agentGuid"]
            conf = data["configuration"]
            command_id = data["commandId"]
            template_id = data["templateId"]
            if self.on_command is not None:
                self.on_command(guid, template_id, command_id, conf)

    def on_error(self, ws, error):
        self.print_info("[ERROR]\n" + error + "\n\n")

    def on_close(self, ws, close_status_code, close_msg):
        self.print_info("[INFO]\nCONNECTION TO C2 WS FAILED\n\n")

    def on_open(self, ws):
        self.print_info("[INFO]\nCONNECTION TO C2 SUCCESSFUL\n\n")
        if self.on_connect is not None:
            self.on_connect()
        
    def running_thread(self):
        self.ws.run_forever(reconnect=5)
        
    def connect(self, host, ip, on_command = None, on_connect = None):    
        self.on_command = on_command
        self.on_connect = on_connect
        self.ws = websocket.WebSocketApp("ws://%s:%d/" % (host, ip), on_open=self.on_open, on_message=self.on_message, on_error=self.on_error, on_close=self.on_close)
        self.main_thread = threading.Thread(target=self.running_thread, args=())
        self.main_thread.start()
        
    def register_commands(self, commands, agent_id = None): 
        msg = {"type": "register-command-templates", "commandTemplates": commands.get_commands(), "validateUsingSchema": True}        
        if agent_id is not None:
            msg["restrictToAgent"] = agent_id
        self.print_info("[MESSAGE TO C2]\n" + json.dumps(msg) + "\n\n")
        self.ws.send(json.dumps(msg))
        
    def new_agent(self, guid, metadata = {}, commands = None):
        msg = {"type": "register-agent"}
        metadata["guid"] = guid
        msg["agentMetadata"] = metadata
        self.print_info("[MESSAGE TO C2]\n" + json.dumps(msg) + "\n\n")
        self.ws.send(json.dumps(msg))   
        if commands is not None:
            msg = {"type": "register-command-templates", "commandTemplates": commands.get_commands(), "restrictToAgent": guid, "validateUsingSchema": True}
            self.print_info("[MESSAGE TO C2]\n" + json.dumps(msg) + "\n\n")
            self.ws.send(json.dumps(msg))

    def new_result(self, agent_guid, command_id, success, result_txt = [], result_binary = {}, error_msg = ""):
        results = []
        for result_name in result_txt:
            result_element = {"type": "text", "name": result_name, "value": result_txt[result_name]}
            results.append(result_element)
        for result_name in result_binary:
            result_element = {"type": "binary", "name": result_name, "value": base64.b64encode(result_binary[result_name]).encode("ascii")}
            results.append(result_element)
    
        status = "success"
        if not success:
            status = "failed"
    
        msg = {
            "type": "send-command-result",
            "agentGuid": agent_guid,
            "commandId": command_id,
            "status": status,
            "results": results,
            "errorMessage": error_msg
        }
        self.print_info("[MESSAGE TO C2]\n" + json.dumps(msg) + "\n\n")
        self.ws.send(json.dumps(msg)) 
        
    def command_sent(self, command_id):
        msg = {
            "type": "command-received",
            "commandId": command_id,
            "commandStartSuccessful": True
        }
        self.print_info("[MESSAGE TO C2]\n" + json.dumps(msg) + "\n\n")
        self.ws.send(json.dumps(msg)) 
        
        
class ExternalListenerCommands:
    def __init__(self):
        self.commands = []
        
    def add_command(self, cmd_id, cmd_desc, cmd_default_conf, cmd_conf_schema):
        if "$schema" not in cmd_conf_schema:
            cmd_conf_schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        if "type" not in cmd_conf_schema:
            cmd_conf_schema["type"] = "object"
        self.commands.append({
            "id": cmd_id,
            "description": cmd_desc,
            "defaultConfiguration": cmd_default_conf,
            "configurationSchema": cmd_conf_schema
        })
        
    def add_command_simple(self, cmd_id, cmd_desc, params):
        cmd_default_conf = {}
        cmd_conf_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {},
            "required": []
        }
        for param_name in params:
            param = params[param_name]
            if "default" in param and param["default"] is not None:
                cmd_default_conf[param_name] = param["default"]
            if "required" in param and param["required"]:
                cmd_conf_schema["required"].append(param_name)
            cmd_conf_schema["properties"][param_name] = {"type": param["type"]}
        self.add_command(cmd_id, cmd_desc, cmd_default_conf, cmd_conf_schema) 
         
    def get_commands(self):
        return self.commands
        