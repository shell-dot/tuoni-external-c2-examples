# Tuoni external listener examples

Python helper library and development examples for Tuoni's external WebSocket
listener.

## Start here

| Component | Documentation |
| --- | --- |
| HTTP controller and sample agents | [HTTP transport](examples/http-transport/README.md) |
| Metasploit session bridge | [Metasploit bridge](examples/metasploit-bridge/README.md) |
| Python helper library | [Installation, lifecycle, logging, and API](lib/README.md) |
| Listener configuration and message formats | [Architecture and protocol](docs/architecture.md) |

## Prerequisites

The Python examples require Python 3.9+ and
[uv](https://docs.astral.sh/uv/getting-started/installation/), which resolves
their inline dependencies. Follow example commands from the repository root
unless their documentation says otherwise.

Both integrations need a running Tuoni **External WebSocket Listener**. The
helper connects to `ws://<host>:<port>/`: keep the listener's `contextPath` at
`/`. The supplied integrations do not perform the WebSocket authentication
handshake, so their listener must have `requireJwtAuthentication: false`.
The Metasploit bridge also needs access to Metasploit RPC.

## How it works

```
  Agent  <-- HTTP -->  Controller  <-- WebSocket -->  Tuoni external listener
```

The HTTP controller translates between agent check-ins and Tuoni's WebSocket
messages. Its HTTP endpoint defaults to `127.0.0.1:8080`, which is reachable
only from the controller's machine. This is separate from the Tuoni listener's
WebSocket endpoint.

The Metasploit bridge uses existing RPC sessions instead of the sample agents.
Each example README describes its setup and limitations.

## JWT authentication

The listener protocol supports JWT authentication, but the helper does not send
an `authenticate` message automatically. The controller's Tuoni REST API login
is separate and does not authenticate its WebSocket connection. See
[authentication](docs/architecture.md#authentication) for the protocol details.

While connected to Tuoni, **Settings > Account > Copy session token** provides
the current JWT. These examples use plain HTTP and `ws://`; the helper has no
TLS or custom WebSocket path option.

## Platform notes

Each agent README lists its requirements, command support, and limitations:

- [Python](examples/http-transport/agents/python/README.md): Python 3.9+;
  some commands require Windows.
- [C# / .NET](examples/http-transport/agents/dotnet/README.md): .NET 8 SDK;
  terminal commands use Windows `cmd.exe`.
- [PowerShell](examples/http-transport/agents/powershell/README.md): Windows.
- [Zig](examples/http-transport/agents/zig/README.md): version-specific build
  requirements, `curl`, and Windows APIs.

These are development examples. Their individual READMEs describe the current
limitations.
