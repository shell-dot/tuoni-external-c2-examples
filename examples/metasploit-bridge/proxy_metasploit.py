#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["tuoni-external", "pymetasploit3>=1.0"]
#
# [tool.uv.sources]
# tuoni-external = {path = "../../lib"}
# ///
"""Metasploit bridge - imports existing Metasploit sessions into Tuoni.

Instead of writing a custom agent, this connects to one or more Metasploit RPC
instances and exposes their sessions as Tuoni agents.

Prerequisites:
    A Metasploit RPC endpoint with existing sessions (see README.md).

Usage:
    Edit the configuration below, then:
    uv run proxy_metasploit.py
"""

import logging
import threading
import time
import uuid

from tuoni_external import ExternalListener, ExternalListenerCommands
from tuoni_external.diagnostics import clean_text, configure_logging


logger = logging.getLogger("metasploit_bridge")

# ---------------------------------------------------------------------------
# Configuration - edit these to match your environment
# ---------------------------------------------------------------------------

METASPLOIT_INSTANCES = [
    {
        "nickname": "my_precious",
        "hostname": "127.0.0.1",
        "port": 55552,
        "key": "yourPassword",
    },
]

TUONI_LISTENER = {
    "hostname": "127.0.0.1",
    "port": 12345,
}

MAX_COMMAND_TIMEOUT = 60  # seconds to wait for command output

# ---------------------------------------------------------------------------
# Bridge implementation
# ---------------------------------------------------------------------------


class MetasploitSession:
    """Tracks one Metasploit RPC client's state."""

    def __init__(self, rpc_client, info_str):
        self.rpc = rpc_client
        self.info_str = info_str
        self.consecutive_errors = 0
        self.previous_sessions = {}
        self._cmd_lock = threading.Lock()


class MetasploitProxy:
    def __init__(self, metasploit_configs):
        self.tuoni = ExternalListener()
        self.metasploit_configs = metasploit_configs
        self.instances = []
        self.agent_to_instance = {}
        self.agent_to_session = {}
        self._tracking_thread = None

    @staticmethod
    def _generate_guid(input_string):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, input_string))

    def _on_command(self, guid, cmd_type, command_id, conf):
        instance = self.agent_to_instance.get(guid)
        if instance is None:
            return

        self.tuoni.command_sent(command_id)

        if cmd_type == "info":
            self.tuoni.new_result(
                guid, command_id, True,
                result_txt={"STDOUT": instance.info_str},
            )
        elif cmd_type == "x":
            if not instance._cmd_lock.acquire(blocking=False):
                self.tuoni.new_result(
                    guid, command_id, False,
                    error_msg="Another command is still running",
                )
                return
            t = threading.Thread(
                target=self._exec_command,
                args=(guid, command_id, conf, instance),
                daemon=True,
            )
            t.start()
        else:
            self.tuoni.new_result(
                guid, command_id, False,
                error_msg="Unknown command '%s'" % cmd_type,
            )

    @staticmethod
    def _run_session_command(session, cmd, timeout):
        """Run a command in a Metasploit session (meterpreter or shell)."""
        session.write(cmd + "\n")
        time.sleep(min(timeout, 2))
        output = session.read()
        if not output:
            remaining = timeout - 2
            while remaining > 0 and not output:
                time.sleep(1)
                remaining -= 1
                output = session.read()
        return output

    def _exec_command(self, guid, command_id, conf, instance):
        try:
            cmd = conf.get("c", "help")
            timeout = 1 if cmd.strip().startswith("cd ") else MAX_COMMAND_TIMEOUT
            session_id = self.agent_to_session[guid]
            session = instance.rpc.sessions.session(session_id)
            result = self._run_session_command(session, cmd, timeout)
            self.tuoni.new_result(
                guid, command_id, True,
                result_txt={"STDOUT": result or ""},
            )
        except Exception as e:
            self.tuoni.new_result(guid, command_id, False, error_msg=str(e))
        finally:
            instance._cmd_lock.release()

    @staticmethod
    def _normalize_os(value):
        """Map a freeform OS string to a Tuoni enum (LINUX, WINDOWS, MAC, BSD)."""
        if not value:
            return None
        lower = value.lower()
        if "linux" in lower or "debian" in lower or "ubuntu" in lower or "centos" in lower or "fedora" in lower or "kali" in lower:
            return "LINUX"
        if "mac" in lower or "darwin" in lower or "osx" in lower:
            return "MAC"
        if "windows" in lower or "win" in lower:
            return "WINDOWS"
        if "bsd" in lower:
            return "BSD"
        return None

    @staticmethod
    def _parse_sysinfo(output):
        """Parse meterpreter sysinfo output into a dict."""
        result = {}
        for line in output.strip().split("\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip()
        return result

    def _register_agent(self, session_id, instance, session):
        guid = self._generate_guid(session["uuid"])
        self.agent_to_instance[guid] = instance
        self.agent_to_session[guid] = session_id

        metadata = {}
        if "username" in session:
            metadata["username"] = session["username"]
        if "platform" in session:
            normalized = self._normalize_os(session["platform"])
            if normalized:
                metadata["os"] = normalized
        if "arch" in session and session["arch"].lower() in ("x64", "x86"):
            metadata["processArch"] = session["arch"]
        if "info" in session and "@" in session["info"]:
            host_part = session["info"][session["info"].find("@") + 1:].strip()
            if " " not in host_part and ":" not in host_part:
                metadata["hostname"] = host_part
        if "session_host" in session:
            metadata["ips"] = session["session_host"]

        if session.get("type") == "meterpreter":
            try:
                sess_obj = instance.rpc.sessions.session(session_id)
                sysinfo_raw = self._run_session_command(sess_obj, "sysinfo", 10)
                if sysinfo_raw:
                    sysinfo = self._parse_sysinfo(sysinfo_raw)
                    if "Computer" in sysinfo:
                        metadata["hostname"] = sysinfo["Computer"]
                    if "OS" in sysinfo:
                        metadata["os"] = self._normalize_os(sysinfo["OS"])
                    if "Architecture" in sysinfo:
                        arch = sysinfo["Architecture"]
                        if "x64" in arch or "64" in arch:
                            metadata["processArch"] = "x64"
                        elif "x86" in arch or "32" in arch:
                            metadata["processArch"] = "x86"
                    logger.debug("session_metadata_received", extra={"session_id": clean_text(session_id)})
            except Exception as e:
                logger.warning("session_metadata_failed", extra={
                    "session_id": clean_text(session_id), "error": clean_text(e),
                })

        self.tuoni.new_agent(guid, metadata=metadata)

        commands = ExternalListenerCommands()
        commands.add_command_simple("info", "Info about the Metasploit instance", {})
        commands.add_command_simple("x", "Run command in Metasploit session", {
            "c": {"default": "help", "required": True, "type": "string"},
        })
        self.tuoni.register_commands(commands, guid)
        logger.info("agent_registration_submitted", extra={
            "agent_guid": guid, "session_id": clean_text(session_id),
        })

    def _on_connect(self):
        logger.info("bridge_connected")
        self.instances = []
        self.agent_to_instance = {}
        self.agent_to_session = {}

        from pymetasploit3.msfrpc import MsfRpcClient

        for conf in self.metasploit_configs:
            logger.info("metasploit_connecting", extra={
                "host": clean_text(conf["hostname"]), "port": conf["port"],
            })
            try:
                rpc = MsfRpcClient(conf["key"], port=conf["port"], host=conf["hostname"])
                info_str = conf.get("nickname", "") + " running on " + conf["hostname"]
                instance = MetasploitSession(rpc, info_str.strip())

                sessions = rpc.sessions.list
                logger.info("metasploit_sessions_found", extra={"count": len(sessions)})
                for sid in sessions:
                    self._register_agent(sid, instance, sessions[sid])

                instance.previous_sessions = dict(sessions)
                self.instances.append(instance)
            except Exception as e:
                logger.error("metasploit_connection_failed", extra={
                    "host": clean_text(conf["hostname"]), "port": conf["port"],
                    "error": clean_text(e),
                })

        if self._tracking_thread is None or not self._tracking_thread.is_alive():
            self._tracking_thread = threading.Thread(target=self._tracking_loop, daemon=True)
            self._tracking_thread.start()

    def _tracking_loop(self):
        """Poll Metasploit instances for new sessions."""
        logger.info("session_tracking_started")
        while True:
            time.sleep(4)
            for instance in self.instances:
                if instance.consecutive_errors >= 5:
                    continue
                try:
                    sessions = instance.rpc.sessions.list
                    instance.consecutive_errors = 0
                    for sid in sessions:
                        if sid not in instance.previous_sessions:
                            self._register_agent(sid, instance, sessions[sid])
                    instance.previous_sessions = dict(sessions)
                except Exception as e:
                    instance.consecutive_errors += 1
                    logger.error("session_tracking_failed", extra={
                        "instance": clean_text(instance.info_str),
                        "consecutive_errors": instance.consecutive_errors,
                        "error": clean_text(e),
                    })

    def connect(self, host, port):
        self.tuoni.connect(host, port, on_command=self._on_command, on_connect=self._on_connect)


def main():
    configure_logging()
    try:
        proxy = MetasploitProxy(METASPLOIT_INSTANCES)
        proxy.connect(TUONI_LISTENER["hostname"], TUONI_LISTENER["port"])
        logger.info("bridge_running")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("bridge_stopping", extra={"reason": "keyboard_interrupt"})
    except Exception:
        logger.exception("bridge_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
