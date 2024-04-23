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

# You have to run msgrpc loaded on metasploit
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
		self.agent_con_infos = {}

	def generate_guid(self, input_string):
		namespace = uuid.NAMESPACE_DNS
		guid = uuid.uuid5(namespace, input_string)
		return str(guid)

	def new_command_arrived(self, guid, cmd_type, command_id, conf):
		if cmd_type == "info" and guid in self.agent_con_infos:
			self.tuoni.new_result(guid,  command_id, True, result_txt = {"STDOUT": self.agent_con_infos[guid]})

	def reg_new_agent(self, metasploit_info, session):
		print("New agent")
		guid = self.generate_guid(session["uuid"])
		self.agent_con_infos[guid] = metasploit_info
		metadata = {}
		if "username" in session:
			metadata["username"] = session["username"]
		if "platform" in session:
			metadata["os"] = session["platform"]
		if "arch" in session:
			metadata["processArch"] = session["arch"]
		if "info" in session and "@" in session["info"]:
			metadata["hostname"] = session["info"][session["info"].find("@")+1 : ].strip()
		if "session_host" in session:
			metadata["ips"] = session["session_host"]
		metadata["customProperties"] = {"notes": "TEST"}
		self.tuoni.new_agent(guid, metadata = metadata)
		commands = ExternalListenerCommands()
		commands.add_command_simple("info", "Info about metasploit server the connection came", {})
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
					self.reg_new_agent(metasploit.info_str, sessions[session_id])
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
						self.reg_new_agent(metasploit.info_str, sessions[session_id])
					metasploit.previous_sessions = sessions.copy()
				except Exception as error:
					print("Pulling sessions from metasploit failed: " + metasploit.info_str)
					print(error)
					metasploit.active = False

	
	def connect(self, host, port):
		self.tuoni.connect(host, port, on_command = self.new_command_arrived, on_connect =self.when_connected)  

proxy = MetasploitProxy(metsaploit_confs)
proxy.connect(tuoni_conf["hostname"], tuoni_conf["port"])