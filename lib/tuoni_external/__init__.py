"""Tuoni external listener - Python WebSocket client and command-template helpers.

Connect a custom transport or third-party tool to a Tuoni server through its
external WebSocket listener without modifying the server itself.

Classes:
    ExternalListener       - WebSocket client that talks to the Tuoni listener.
    ExternalListenerCommands - Builder for the command templates the listener advertises.
"""

import base64
import json
import logging
import threading

import websocket

from .diagnostics import clean_text, log_payload


logger = logging.getLogger("tuoni_external.websocket")


class ExternalListener:
    """WebSocket client for a Tuoni external listener.

    Connects to the listener, receives command dispatches, and sends back agent
    registrations, command acknowledgements, and results.

    Logging uses ``tuoni_external.websocket`` and the application's logging
    configuration. ``log_payloads`` enables redacted, bounded payload previews
    at DEBUG level; ``payload_limit`` limits each preview's character count.
    ``verbose`` is retained as an alias for enabling those previews.
    """

    def __init__(self, verbose=False, *, log_payloads=False, payload_limit=1024):
        self._ws = None
        self._thread = None
        self._on_command = None
        self._on_connect = None
        self.verbose = verbose
        self.log_payloads = log_payloads
        self.payload_limit = payload_limit

    # -- internal callbacks ---------------------------------------------------

    def _log_frame(self, direction, payload, data):
        if not logger.isEnabledFor(logging.DEBUG):
            return
        fields = {
            "direction": direction,
            "bytes": len(payload.encode("utf-8", errors="replace"))
            if isinstance(payload, str) else len(payload),
            "message_type": "unknown",
        }
        if isinstance(data, dict):
            message_type = data.get("type")
            if isinstance(message_type, (str, int, float, bool)):
                fields["message_type"] = clean_text(message_type)
            for source, target in (
                ("agentGuid", "agent_guid"),
                ("commandId", "command_id"),
                ("templateName", "template_name"),
                ("restrictToAgent", "restrict_to_agent"),
            ):
                value = data.get(source)
                if isinstance(value, (str, int, float, bool)):
                    fields[target] = clean_text(value)
        logger.debug("websocket_frame", extra=fields)
        log_payload(
            logger, "websocket_payload", data,
            enabled=self.log_payloads or self.verbose,
            limit=self.payload_limit,
            **fields,
        )

    def _on_message(self, ws, message):
        data = json.loads(message)
        self._log_frame("incoming", message, data)
        if data.get("type") == "error":
            reason = data.get("error", "Server reported an error")
            logger.error("websocket_server_error", extra={
                "error": clean_text(reason)
                if isinstance(reason, (str, int, float, bool))
                else "Server reported an error",
            })
        if data.get("type") == "start-command":
            guid = data["agentGuid"]
            conf = data["configuration"]
            command_id = data["commandId"]
            template_id = data["templateName"]
            if self._on_command is not None:
                self._on_command(guid, template_id, command_id, conf)

    def _on_error(self, ws, error):
        exception = (
            (type(error), error, error.__traceback__)
            if isinstance(error, BaseException) else None
        )
        logger.error("websocket_error", exc_info=exception, extra={
            "error_type": type(error).__name__,
            "error": clean_text(error),
        })

    def _on_close(self, ws, close_status_code, close_msg):
        logger.warning("websocket_closed", extra={
            "close_code": close_status_code,
            "close_reason": clean_text(close_msg or ""),
        })

    def _on_open(self, ws):
        logger.info("websocket_connected")
        if self._on_connect is not None:
            self._on_connect()

    def _run(self):
        try:
            self._ws.run_forever(reconnect=5)
        except Exception as error:
            # URL/setup failures can occur before websocket-client's own handler.
            self._on_error(self._ws, error)

    # -- public API -----------------------------------------------------------

    def connect(self, host, port, on_command=None, on_connect=None):
        """Open a WebSocket connection to the Tuoni external listener.

        Args:
            host: Listener hostname or IP.
            port: Listener port (int, 1 through 65535).
            on_command: Callback(guid, template_id, command_id, conf) for
                incoming command dispatches.
            on_connect: Callback() fired once the WebSocket is open.
        """
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("listener port must be an integer between 1 and 65535")
        self._on_command = on_command
        self._on_connect = on_connect
        url = "ws://%s:%d/" % (host, port)
        logger.info("websocket_connecting", extra={
            "host": clean_text(host),
            "port": port,
        })
        self._ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="tuoni-websocket",
        )
        self._thread.start()

    def register_commands(self, commands, agent_id=None):
        """Register command templates with the listener.

        Args:
            commands: An ExternalListenerCommands instance.
            agent_id: Optional agent GUID to restrict templates to.
        """
        msg = {
            "type": "register-command-templates",
            "commandTemplates": commands.get_commands(),
            "validateUsingSchema": True,
        }
        if agent_id is not None:
            msg["restrictToAgent"] = agent_id
        self._send(msg)

    def new_agent(self, guid, metadata=None, commands=None):
        """Register a new agent with the Tuoni server.

        Args:
            guid: Unique agent identifier (string).
            metadata: Optional dict of agent metadata (hostname, username, ...).
                The dict is not mutated.
            commands: Optional ExternalListenerCommands to register for this
                agent immediately.
        """
        agent_meta = dict(metadata) if metadata else {}
        agent_meta["guid"] = guid
        msg = {"type": "register-agent", "agentMetadata": agent_meta}
        self._send(msg)
        if commands is not None:
            self.register_commands(commands, agent_id=guid)

    def new_result(self, agent_guid, command_id, success, result_txt=None, result_binary=None, error_msg=""):
        """Send a command result back to the Tuoni server.

        Args:
            agent_guid: The agent that executed the command.
            command_id: The command ID being answered.
            success: True for success, False for failure.
            result_txt: Optional dict {name: text_value} of text results.
            result_binary: Optional dict {name: bytes_value} of binary results.
            error_msg: Error message string on failure.
        """
        results = []
        if result_txt:
            for name, value in result_txt.items():
                results.append({"type": "text", "name": name, "value": value})
        if result_binary:
            for name, value in result_binary.items():
                results.append({
                    "type": "binary",
                    "name": name,
                    "value": base64.b64encode(value).decode("ascii"),
                })
        msg = {
            "type": "send-command-result",
            "agentGuid": agent_guid,
            "commandId": command_id,
            "status": "success" if success else "failed",
            "results": results,
            "errorMessage": error_msg,
        }
        self._send(msg)

    def command_sent(self, command_id):
        """Acknowledge that a command was received and is being executed."""
        msg = {
            "type": "command-received",
            "commandId": command_id,
            "commandStartSuccessful": True,
        }
        self._send(msg)

    def _send(self, msg):
        payload = json.dumps(msg)
        self._log_frame("outgoing", payload, msg)
        self._ws.send(payload)


class ExternalListenerCommands:
    """Builder for command templates that an external listener advertises.

    Each command has a name, description, default configuration, and a JSON
    Schema describing its configuration parameters.
    """

    def __init__(self):
        self._commands = []

    def add_command(self, name, description, default_conf, conf_schema):
        """Add a command with an explicit JSON Schema.

        The schema dict is copied - the caller's original is not mutated.
        """
        schema = dict(conf_schema)
        schema.setdefault("$schema", "https://json-schema.org/draft/2020-12/schema")
        schema.setdefault("type", "object")
        self._commands.append({
            "name": name,
            "description": description,
            "defaultConfiguration": dict(default_conf),
            "configurationSchema": schema,
        })

    def add_command_simple(self, name, description, params):
        """Add a command from a simplified parameter dict.

        Args:
            name: Command identifier.
            description: Human-readable description.
            params: Dict of {param_name: {type, default?, required?}}.
        """
        default_conf = {}
        properties = {}
        required = []
        for param_name, param in params.items():
            if param.get("default") is not None:
                default_conf[param_name] = param["default"]
            if param.get("required"):
                required.append(param_name)
            properties[param_name] = {"type": param["type"]}
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": properties,
            "required": required,
        }
        self.add_command(name, description, default_conf, schema)

    def get_commands(self):
        """Return the list of command template dicts."""
        return list(self._commands)
