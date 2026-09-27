# Zig agent

Windows HTTP polling example targeting the
[Zig 0.16.0 APIs](https://ziglang.org/documentation/0.16.0/).
Native Windows builds have not been verified as part of the repository checks;
compatibility with later compiler releases is not established. The runtime requires `curl`,
`cmd.exe`, and `powershell.exe` on PATH. Native Linux/macOS execution is not
supported by the current Windows API bindings and metadata collection.

Follow the [controller setup](../../README.md#quick-start) first. From the
repository root, replacing the placeholders with the controller's HTTP address
and port:

```sh
cd examples/http-transport/agents/zig
zig build run -- <controller_host> <controller_port>
```

The agent supports `my_what`, `my_sleep`, and `my_terminal`. The source also
contains the experimental Windows-specific `my_spawn` command. `my_eval` is
not supported.

## Current limitations

- The response parser does not decode JSON string escapes, so quotes,
  backslashes, and Unicode escapes in parameters can be interpreted incorrectly.
- Responses to result uploads are discarded, including any next command.
- HTTP requests have no explicit timeout; curl failures and HTTP error status
  codes are not checked.
- Terminal stderr and exit status are discarded. Execution failures can appear
  as successful results, and polling intervals are not range-checked.

See the [shared limitations](../../README.md#current-limitations) for controller
behavior. A successful build alone does not validate these runtime paths.
