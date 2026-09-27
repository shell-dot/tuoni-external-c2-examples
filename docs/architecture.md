# External listener architecture

How Tuoni's external listener works and how to use it from your own code.

## The extension point

Tuoni exposes an **External WebSocket Listener** that third-party code connects
to. Through this WebSocket your code can:

- Register **agents** with metadata (hostname, username, OS, IPs).
- Declare **command templates** that appear in the Tuoni UI for those agents.
- Receive **command dispatches** when an operator issues a command.
- Send **command results** back.

This lets you build custom transports, bridge other tools, or add agents on
platforms Tuoni does not natively support - without modifying the server.

## Creating the listener

In the Tuoni UI, go to **Listeners** and create a new **External WebSocket
Listener**. The configuration fields are:

| Field | Default | Description |
| --- | --- | --- |
| `host` | `null` | Bind hostname or IP address; `null` binds to all interfaces |
| `port` | `8123` | WebSocket port your code connects to |
| `contextPath` | `/` | URL path for the WebSocket endpoint |
| `requireJwtAuthentication` | `false` | When `true`, clients must authenticate with a Tuoni JWT before any other message is accepted |
| `restrictDataToUser` | `false` | When `true` (requires JWT), restrict agents with an associated user to that user; ownerless agents remain shared |

Once started, the listener opens a WebSocket server on the configured port.
The Python helper currently connects to `ws://<host>:<port>/`; it does not expose
configuration for a different context path or TLS.

## Three-part pattern (HTTP transport)

The HTTP transport example uses three components:

```
  Target machine           Your infrastructure           Tuoni server
 +-------------+         +------------------+         +------------------+
 |             |         |                  |         |                  |
 | Sample agent|--HTTP-->|   Controller     |---WS--->| External listener|
 |             |         |  (Python)        |         |  (WebSocket)     |
 +-------------+         +------------------+         +------------------+
```

### Why a controller?

The agent doesn't talk WebSocket directly. Instead, a Python controller sits in
the middle and translates:

- **Agent -> Controller**: plain HTTP POST with JSON (agent check-in, results).
- **Controller -> Tuoni**: WebSocket messages (agent registration, results).
- **Tuoni -> Controller**: WebSocket messages (command dispatches).
- **Controller -> Agent**: HTTP response JSON (queued commands).

This separation means:
1. Agents can be extremely simple (just an HTTP POST loop).
2. The transport is swappable - replace HTTP with DNS, ICMP, or anything else.
3. The controller handles the registration, command, and result messages used by
   this example.

### Two-part pattern (Metasploit bridge)

The Metasploit bridge skips the agent entirely:

```
  Metasploit RPC ------> Proxy (Python) -----WS-----> Tuoni external listener
```

The proxy discovers sessions through Metasploit's RPC API and registers them as
Tuoni agents directly.

## WebSocket protocol

This section describes the WebSocket messages used by these examples. The
`tuoni_external` library (`lib/`) provides helpers for registration, command
dispatch, acknowledgements, and results. Authentication is not handled
automatically by the library.

Every message is a JSON object with a `type` field.

### Client -> Tuoni

| Type | Purpose | Key fields |
| --- | --- | --- |
| `authenticate` | Authenticate with a JWT (required when `requireJwtAuthentication` is enabled) | `jwtToken` |
| `register-agent` | Announce a new agent | `agentMetadata: {guid, username?, hostname?, os?, ips?}` |
| `register-command-templates` | Declare available commands | `commandTemplates: [...], restrictToAgent?, validateUsingSchema` |
| `command-received` | Acknowledge a dispatched command | `commandId, commandStartSuccessful, failureMessage?` |
| `send-command-result` | Return command output | `agentGuid, commandId, status, results: [{type, name, value}], errorMessage?` |

### Tuoni -> Client

| Type | Purpose | Key fields |
| --- | --- | --- |
| `authenticated` | JWT accepted | `name` |
| `accepted` | Command-template registration or command acknowledgement accepted | `message` |
| `agent` | Agent registration or status response | `agentGuid, agentMetadata, active` |
| `start-command` | Dispatch a command to an agent | `agentGuid, commandId, templateName, agentMetadata, configuration` |
| `error` | Something went wrong | `error, stacktrace` (nullable) |

## Authentication

When the listener has `requireJwtAuthentication: true`, the first message after
connecting **must** be an `authenticate` message. Any other message sent before
authentication returns an error and is dropped.

### Getting a JWT token

**From the UI:** Go to **Settings > Account > Copy session token**.

**From the API:**

```
POST /api/v1/auth/login
Authorization: Basic <base64(username:password)>
```

The response body is the JWT token string.

### Sending it over WebSocket

```json
{"type": "authenticate", "jwtToken": "<token from the login call>"}
```

Tuoni responds with `{"type": "authenticated", "name": "<username>"}` on success.

After that, send `register-agent`, `register-command-templates`, etc. as usual.

### When `restrictDataToUser` is also enabled

Access to agents with an associated authenticated user is restricted to that
user, and their commands are routed to that user's connections. Agents without
an associated user remain accessible across users, and their commands are
broadcast to all connections. This setting does not provide complete isolation
for ownerless agents. It controls this listener's WebSocket connections, not
visibility in the Tuoni UI.

### When authentication is disabled

No `authenticate` message is needed. Connect and start sending `register-agent`
messages directly. This is the default and what the example WebSocket
connections in this repo use.

The `tuoni_external` library has no public authentication method and does not
perform this handshake. The HTTP controller's optional REST API login is
separate: obtaining an API JWT does not authenticate its WebSocket connection.

## Agent metadata fields

When registering an agent, the following metadata fields are recognized:

| Field | Type | Description |
| --- | --- | --- |
| `guid` | UUID string | Unique agent identifier (required) |
| `username` | string | User the agent runs as |
| `hostname` | string | Machine hostname |
| `os` | string | Operating system enum: `LINUX`, `WINDOWS`, `MAC`, or `BSD` |
| `ips` | string | IP addresses |
| `processArch` | string | Architecture enum: `X86`, `X64`, `ARM32`, or `ARM64` |
| `customProperties` | object | Present in the message schema, but ignored by the current server's registration/update conversion; these values are not persisted as UI metadata |

## Gotchas and known issues

These notes distinguish the server's protocol from the helper library and
example implementations.

### OS and architecture fields use enums

The `os` enum values are `LINUX`, `WINDOWS`, `MAC`, and `BSD`. The server accepts
case-insensitive enum names. Freeform OS version strings such as
`"Microsoft Windows NT 10.0"` are not enum names and fail deserialization.
The Metasploit bridge's `_normalize_os()` maps some common OS names before
registration.

`processArch` also uses a case-insensitive enum: `X86`, `X64`, `ARM32`, or `ARM64`.
Aliases such as `amd64` and `aarch64` are not recognized.

### Command delivery is shared within the configured scope

With `restrictDataToUser: false`, command dispatches are broadcast to all
connections registered with the listener. With the restriction enabled,
user-associated agents' dispatches go to all connections authenticated as that
user; ownerless agents remain shared. There is no routing to one specific
controller within those groups.

The current examples handle commands for unknown agents differently: the
Metasploit bridge ignores them, while the HTTP controller can send a failure
result. Sharing a delivery scope between them can therefore produce competing
results for the same command.

### Duplicate registration does not update an agent

When an agent GUID already exists, `register-agent` returns an
`"agent with guid=X already registered"` error and leaves the existing record
unchanged. This response is not an acknowledgement of refreshed metadata or
restored command delivery after reconnecting.

### Metadata updates exist in the server protocol

The server supports `update-metadata` with `agentGuid` and `agentMetadata` fields.
It returns an `accepted` response while the update is pending and an `agent`
response after a successful update. The Python helper does not expose a public
metadata update method; repeating `new_agent()` does not perform an update.

### Example-specific commands are separate from the listener protocol

The HTTP example includes an optional Windows-only `my_spawn` command, enabled
for its supported agent types after a successful REST API login. This is an
example command template, not a built-in WebSocket message. Its REST API
credentials do not change the helper's WebSocket authentication behavior.
