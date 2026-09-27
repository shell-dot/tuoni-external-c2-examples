# HTTP transport example

Agents POST JSON to a Python controller, which exchanges registrations, commands,
and results with Tuoni over WebSocket.

```text
Agent <-- HTTP --> controller.py <-- WebSocket --> Tuoni external listener
```

## Quick start

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/), Python
3.9+, and a running Tuoni **External WebSocket Listener**. These examples do
not authenticate their WebSocket connection; the listener must allow that.

Run from the repository root, replacing the angle-bracket placeholders:

```sh
uv run examples/http-transport/controller.py <ws_host> <ws_port>
```

The HTTP endpoint defaults to `127.0.0.1:8080`, accessible only on the
controller's machine. Its bind address and port are optional positional
arguments after the WebSocket host and port. HTTP port `0` selects an available
port, reported in the startup log. The HTTP and WebSocket ports are different
endpoints.

Start an agent in a separate terminal using its README:

| Agent | Runtime requirements |
| --- | --- |
| [Python](agents/python/README.md) | Python 3.9+ and uv; Windows, Linux, or macOS |
| [C# / .NET](agents/dotnet/README.md) | Windows and .NET 8 SDK |
| [PowerShell](agents/powershell/README.md) | Windows PowerShell 5.1 or PowerShell 7 on Windows |
| [Zig](agents/zig/README.md) | Windows, Zig 0.16.0, and curl |

The agent's `controller_host` and `controller_port` refer to the HTTP endpoint.
After registration, the agent and its command templates appear in Tuoni.

## Developer logging

The controller writes logs to stderr. INFO shows startup, connection changes,
registration attempts, and results; DEBUG adds command queuing, WebSocket
messages, and routine polls. Unexpected exceptions include tracebacks.

From the repository root:

```sh
uv run examples/http-transport/controller.py <ws_host> <ws_port> \
  --log-level DEBUG --log-format json --log-file controller.log
```

| Option | Default | Purpose |
| --- | --- | --- |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `--log-format` | `text` | Text lines or JSON records |
| `--log-file` | None | Also write a rotating UTF-8 log file |
| `--log-max-bytes` | `5242880` | File rotation threshold, in bytes |
| `--log-backups` | `3` | Rotated files to retain |
| `--log-payloads` | Off | Redacted envelope previews at DEBUG level |
| `--log-payload-limit` | `1024` | Preview character limit |

Numeric logging limits must be positive. Records include UTC timestamps,
logger/thread names, and available context. HTTP request IDs correlate request
diagnostics with completion records containing status, duration, and body sizes.
Unknown sizes are `null`. WebSocket close records include codes and reasons.

Payload previews require both DEBUG and `--log-payloads`. They redact
configuration, results, metadata, credentials, and unknown values, and escape
control characters. Exception messages may contain application data; review
logs before sharing them. See the [library logging reference](../../lib/README.md#logging)
for use in another application.

Run the offline logging and argument-validation tests from the repository root:

```sh
uv run --no-project --with 'websocket-client>=1.6.0,<2' --with 'requests>=2.28,<3' python -B -m unittest discover -s tests -v
```

These checks use fixtures; they do not validate native agent builds or live
Tuoni/Metasploit integration.

## Commands

| Command | Behavior | Agent types |
| --- | --- | --- |
| `my_terminal` | Run a shell command | All |
| `my_sleep` | Change polling interval in seconds | All |
| `my_what` | Return agent identification | All |
| `my_eval` | Evaluate an expression in the agent's runtime | Python, PowerShell |

The source also contains an experimental, Windows-specific `my_spawn` command,
registered only after the controller obtains a Tuoni API token. That API login
does **not** authenticate the WebSocket connection.

The current `my_eval` default is Python-specific and returns `None` in Python;
it is not a useful shared default for both runtimes. Sleep values are not
range-checked, and negative or very large values can interrupt polling.

## HTTP message format

A check-in is a JSON object. Recognized agent types are `python`, `dotNET`,
`powershell`, and `zig`:

```json
{"id": "<uuid>", "type": "python", "username": "user", "hostname": "host"}
```

Optional metadata fields are `username`, `hostname`, `os`, `processArch`, and
`ips`. They are forwarded when the controller first registers the agent.
`os` must use the server's enum values (`LINUX`, `WINDOWS`, `MAC`, or `BSD`),
not a freeform OS description. See the [metadata reference](../../docs/architecture.md#agent-metadata-fields).
The sample architecture detection assumes x86/x64 and can misreport ARM systems.

A result check-in additionally contains `"result": "<output>"` and optionally
`"success": false`; success defaults to true. The response is either `{}` or
a command object with a `__type__` field and its parameters. A result upload
can receive another command in its response.

## Current limitations

This example keeps command state in memory. Results do not carry command IDs,
so interrupted deliveries and retries can lose commands or associate results
incorrectly. Reconnection clears the controller's agent state. Threaded HTTP
handlers also share this state without synchronization.

Command failure reporting and execution timeouts differ between agents; see
their READMEs. The controller does not fully validate request types, lengths,
or time limits. It may also return failures for another client's agents when
sharing an external listener. These limitations remain in the implementation.

The optional REST client disables TLS certificate verification. Credentials
passed as command-line flags can appear in shell history and process listings.
