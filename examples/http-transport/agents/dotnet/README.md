# C# / .NET agent

Windows HTTP polling example targeting .NET 8. Install the
[.NET 8 SDK](https://dotnet.microsoft.com/en-us/download/dotnet/8.0) and follow
the [controller setup](../../README.md#quick-start) first. The implementation
reports Windows metadata and uses `cmd.exe` for terminal commands.

From the repository root, replacing the placeholders with the controller's
HTTP address and port:

```sh
dotnet run --project examples/http-transport/agents/dotnet -- <controller_host> <controller_port>
```

`dotnet run` restores and builds the project before starting it. Supported
commands are `my_what`, `my_sleep`, and `my_terminal`; the source also contains
the experimental Windows-specific `my_spawn` command. `my_eval` is not
registered for this agent.

The terminal timeout is currently ineffective while output streams remain
open. Execution failures are returned as ordinary successful results. Large
sleep values can overflow the seconds-to-milliseconds conversion, and negative
values can stop the agent. Pending output is cleared before response JSON is
validated. See the [shared limitations](../../README.md#current-limitations).
