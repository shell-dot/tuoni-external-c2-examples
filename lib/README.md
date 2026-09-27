# tuoni_external - Python helper library

Python WebSocket client and command-template helpers for Tuoni's external
listener. Requires Python 3.9+.

## Install

The [HTTP controller](../examples/http-transport/README.md) and
[Metasploit bridge](../examples/metasploit-bridge/README.md) declare this library
as an inline dependency. Running them with [uv](https://docs.astral.sh/uv/)
handles their installation automatically.

To add the library to your own uv project, run this from the directory containing
that project's `pyproject.toml`. Replace the absolute path with your checkout:

```sh
uv add /absolute/path/to/tuoni-external-c2-examples/lib
```

Alternatively, create a virtual environment and install the library into it.
Run these commands from this repository's root:

```sh
uv venv
uv pip install ./lib
```

Use the created `.venv` interpreter for your application; installation does not
activate it in your shell.

## Lifecycle and limits

`connect()` starts a background daemon thread and returns immediately. The
application must keep its main thread alive. `on_connect()` runs when the
WebSocket opens; `on_command(guid, template_id, command_id, conf)` receives
command dispatches on that worker thread.

Agent registration and command-template registration are separate operations.
Registering templates alone does not create an agent in Tuoni. The application
owns agent state, command handling, and result reporting; the library does not
implement execution or restore application state after a reconnect.

The endpoint is always `ws://<host>:<port>/`, with an integer port from 1 to
65535. TLS and custom WebSocket paths are not configurable. JWT authentication
is not automatic; see the
[protocol reference](../docs/architecture.md#authentication) for the handshake.
Transport reconnection is delegated to `websocket-client` and does not guarantee
delivery or replay of registrations and results.

## Logging

The library uses Python's `logging` module with the logger
`tuoni_external.websocket`. It does not configure handlers or change the
application's logging level. Configure logging in your application before using
the client, for example:

```python
import logging
from tuoni_external.diagnostics import configure_logging

configure_logging(level="INFO", output_format="text")
logging.getLogger("tuoni_external.websocket").setLevel(logging.DEBUG)
```

Connection events and failures include context; exceptions include tracebacks.
`ExternalListener(log_payloads=True)` enables bounded, redacted payload previews
at DEBUG level. `verbose=True` is a compatibility alias for this option.
Exception messages can still contain application-supplied data; review logs
before sharing them.

`configure_logging` sets up UTC text or JSON output and optional rotating files.
Call it only from your application entry point: it replaces existing root
handlers. Custom handlers can use `DiagnosticFormatter` or include the record's
extra fields in their own formatter. See the
[controller logging options](../examples/http-transport/README.md#developer-logging)
for the available settings and test command.

## API reference

### ExternalListener

| Method | Description |
| --- | --- |
| `ExternalListener(verbose=False, *, log_payloads=False, payload_limit=1024)` | Create a client; payload-preview options do not configure logging handlers or levels. |
| `connect(host, port, on_command=None, on_connect=None)` | Start the WebSocket worker. See lifecycle and endpoint limits above. |
| `new_agent(guid, metadata=None, commands=None)` | Register a new agent. See [agent metadata fields](../docs/architecture.md#agent-metadata-fields) for supported fields and limitations. |
| `register_commands(commands, agent_id=None)` | Declare command templates. Pass `agent_id` to restrict to one agent. |
| `command_sent(command_id)` | Acknowledge receipt of a command dispatch. |
| `new_result(agent_guid, command_id, success, result_txt=None, result_binary=None, error_msg="")` | Send a command result. `result_txt`: `{name: str}`. `result_binary`: `{name: bytes}`. |

### ExternalListenerCommands

| Method | Description |
| --- | --- |
| `add_command_simple(name, description, params)` | Add a command from `{param_name: {type, default?, required?}}`. |
| `add_command(name, description, default_conf, conf_schema)` | Add a command with an explicit JSON Schema for the configuration. |
| `get_commands()` | Return the built command template list. |
