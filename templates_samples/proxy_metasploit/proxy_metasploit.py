try:
    from external_listener import ExternalListener, ExternalListenerCommands
except:
    print("[INFO] external_listener.py file not here, looking from ../../external_listener_lib directory")
    import sys
    sys.path.append('../../external_listener_lib')
    from external_listener import ExternalListener, ExternalListenerCommands
    print("[INFO]   Jep, there it is")
from pymetasploit3.msfrpc import MsfRpcClient
import uuid
import time
import threading

# You have to run msgrpc for connections on metasploit
# > load msgrpc Pass=yourPassword
metsaploit_confs = [
	{"nickname": "my_precious", "hostname": "127.0.0.1", "port": 55552, "key": "yourPassword"}
]
# You have to have external listener running on these configurations
tuoni_conf = {"hostname": "127.0.0.1", "port": 12345}




class MetasploitProxy:
	def __init__(self, metasploit_confs):
		self.tuoni = ExternalListener()
		self.metasploit_confs = metasploit_confs
		self.metasploits = []
		self.loop_thread = None
		self.agent_to_metasploit = {}
		self.agent_to_session = {}
		self.max_wait_on_command_responses = 60

	def generate_guid(self, input_string):
		namespace = uuid.NAMESPACE_DNS
		guid = uuid.uuid5(namespace, input_string)
		return str(guid)

	def new_command_arrived(self, guid, cmd_type, command_id, conf):
		if cmd_type == "info" and guid in self.agent_to_metasploit:
			self.tuoni.new_result(guid,  command_id, True, result_txt = {"STDOUT": self.agent_to_metasploit[guid].info_str})
		if cmd_type == "x" and guid in self.agent_to_metasploit and guid in self.agent_to_session:
			if self.agent_to_metasploit[guid].running_cmd:
				self.tuoni.new_result(guid,  command_id, True, result_txt = {"STDOUT": "Can't run multiple commands in same time, sorry!"})
			else:				
				tmp = threading.Thread(target=self.new_command_exec_thread, args=(guid, cmd_type, command_id, conf))
				tmp.start()
		time.sleep(0.1)

	def new_command_exec_thread(self, guid, cmd_type, command_id, conf):
		timeout = self.max_wait_on_command_responses
		if conf["c"].strip().startswith("cd "):
			timeout = 1;
		if self.agent_to_metasploit[guid].running_cmd:
			self.tuoni.new_result(guid,  command_id, True, result_txt = {"STDOUT": "Can't run multiple commands in same time, sorry!"})
			return
		self.agent_to_metasploit[guid].running_cmd = True
		result = self.agent_to_metasploit[guid].sessions.session(self.agent_to_session[guid]).run_with_output(conf["c"], timeout = timeout, timeout_exception=False)
		if result is None:
			result = ""
		self.tuoni.new_result(guid,  command_id, True, result_txt = {"STDOUT": result})
		self.agent_to_metasploit[guid].running_cmd = False

	def reg_new_agent(self, session_id, metasploit, session):
		print("New agent")
		guid = self.generate_guid(session["uuid"])
		metasploit.running_cmd = False
		self.agent_to_metasploit[guid] = metasploit
		self.agent_to_session[guid] = session_id
		metadata = {}
		if "username" in session:
			metadata["username"] = session["username"]
		if "platform" in session:
			metadata["os"] = session["platform"]
		if "arch" in session and session["arch"].lower() in ["x64","x86"]:
			metadata["processArch"] = session["arch"]
		if "info" in session and "@" in session["info"]:
			tmp = session["info"][session["info"].find("@")+1 : ].strip()
			if " " not in tmp and ":" not in tmp:
				metadata["hostname"] = tmp
		if "session_host" in session:
			metadata["ips"] = session["session_host"]
		metadata["customProperties"] = {"notes": "TEST"}
		self.tuoni.new_agent(guid, metadata = metadata)
		commands = ExternalListenerCommands()
		commands.add_command_simple("info", "Info about metasploit server the connection came", {})
		commands.add_command_simple("x", "Run command in metasploit session", {"c": { "default": "help", "required": True, "type": "string" }})
		self.tuoni.register_commands(commands, guid)  

	def when_connected(self):
		print("Connection to external listener made")
		self.metasploits = []
		for metasploit_conf in self.metasploit_confs:
			metasploit = None
			print("Connecting to metasploit @ %s:%d" % (metasploit_conf["hostname"], metasploit_conf["port"]))
			try:
				metasploit = MsfRpcClient(metasploit_conf["key"], port=metasploit_conf["port"], host=metasploit_conf["hostname"])
				metasploit.info_str = "running on " + metasploit_conf["hostname"]
				if "nickname" in metasploit_conf and len(metasploit_conf["nickname"]) > 1:
					metasploit.info_str = metasploit_conf["nickname"] + "  " + metasploit.info_str
				print("  * Connection successful, pulling connections")
				sessions = metasploit.sessions.list
				print("  * Found %d connections" % len(sessions))
				for session_id in sessions:
					self.reg_new_agent(session_id, metasploit, sessions[session_id])
				metasploit.previous_sessions = sessions.copy()
				metasploit.active = True
				self.metasploits.append(metasploit)
			except Exception as error:
				if metasploit is None:
					print("Connecting to metasploit failed: " + metasploit_conf["hostname"])
				else:
					print("Pulling sessions from metasploit failed: " + metasploit.info_str)
				print(error)
		self.loop_thread = threading.Thread(target=self.tracking_loop, args=())
		self.loop_thread.start()

	def tracking_loop(self):
		print("Tracking thread starting")
		while True:
			time.sleep(4)
			for metasploit in self.metasploits:
				if not metasploit.active:
					continue
				try:
					sessions = metasploit.sessions.list
					for session_id in sessions:
						if session_id in metasploit.previous_sessions:
							continue
						self.reg_new_agent(session_id, metasploit, sessions[session_id])
					metasploit.previous_sessions = sessions.copy()
				except Exception as error:
					print("Pulling sessions from metasploit failed: " + metasploit.info_str)
					print(error)
					metasploit.active = False

	
	def connect(self, host, port):
		self.tuoni.connect(host, port, on_command = self.new_command_arrived, on_connect =self.when_connected)  

proxy = MetasploitProxy(metsaploit_confs)
proxy.connect(tuoni_conf["hostname"], tuoni_conf["port"])