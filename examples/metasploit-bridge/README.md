# Metasploit bridge

Exposes existing Metasploit sessions as Tuoni agents through Metasploit's
MessagePack RPC API.

```text
Metasploit RPC <--> proxy_metasploit.py <-- WebSocket --> Tuoni external listener
```

## Prerequisites and startup

- Python 3.9+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- A Metasploit RPC endpoint exposing the sessions you want to inspect.
- A Tuoni **External WebSocket Listener** allowing unauthenticated connections.

RPC can be provided by `msfrpcd` or the
[`msgrpc` plugin](https://github.com/rapid7/metasploit-framework/blob/master/plugins/msgrpc.rb)
inside `msfconsole`. The plugin exposes its current framework instance;
a separate daemon has separate sessions. Sessions do not need to have been
created through RPC, but they must belong to the instance being queried.

Edit `METASPLOIT_INSTANCES` and `TUONI_LISTENER` in
[proxy_metasploit.py](proxy_metasploit.py). The current client uses the default
RPC username and unencrypted RPC; it does not expose SSL or username settings.
Also see the hostname limitation below.

From the repository root:

```sh
uv run examples/metasploit-bridge/proxy_metasploit.py
```

uv installs the inline dependencies. The bridge registers existing sessions
and checks for new ones after a four-second delay between scans; RPC calls
and metadata collection can make the interval longer.

## Behavior

| Command | Behavior |
| --- | --- |
| `info` | Return the owning Metasploit instance's label |
| `x` | Send a command to the session and return the first nonempty output read |

Shell and Meterpreter sessions use their respective command syntax. The bridge
uses session metadata for username, platform, architecture, and IP address.
For Meterpreter it also attempts `sysinfo` during registration to enrich the
hostname, OS, and architecture. Metadata collection is best effort.

Commands are serialized **per Metasploit instance**, not per session. Unknown
agent GUIDs are ignored. See the [protocol reference](../../docs/architecture.md)
for Tuoni message formats.

The entry point writes structured text logs to stderr at INFO level, including
library connection events and failures. It has no logging command-line options.
See the [library logging reference](../../lib/README.md#logging) for configuration
when embedding the bridge in another application.

## Current limitations

- The RPC client receives `host` instead of its supported `server` setting, so
  configured remote hostnames are ignored and connections use localhost.
- Command output can be incomplete: the bridge stops reading at the first
  nonempty response. Its wait limit is not a process-execution deadline, and an
  empty result is reported as success.
- Five consecutive polling errors permanently disable scanning that instance
  until connection initialization runs again. Closed-session mappings are not
  removed.

Reconnection rebuilds the RPC instances and mappings, but does not recover
in-flight commands. Duplicate registration errors should be investigated;
they are not evidence that reconnection completed successfully.
