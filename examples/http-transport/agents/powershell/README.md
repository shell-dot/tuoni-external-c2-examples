# PowerShell agent

Windows HTTP polling example using Windows PowerShell 5.1 or PowerShell 7 on
Windows. It uses `Invoke-WebRequest` for HTTP and `cmd.exe` for terminal
commands. Follow the [controller setup](../../README.md#quick-start) first.

From the repository root, replacing the placeholders with the controller's
HTTP address and port:

```powershell
powershell -File examples/http-transport/agents/powershell/agent.ps1 <controller_host> <controller_port>
```

For PowerShell 7, use `pwsh` in place of `powershell`. Script execution must be
permitted by your environment's execution policy; see Microsoft's
[execution-policy reference](https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_execution_policies).

The agent supports `my_what`, `my_sleep`, `my_terminal`, and `my_eval`.
`my_eval` uses PowerShell syntax. The source also contains the experimental
Windows-specific `my_spawn` command.

Terminal and expression execution have no timeout. Execution failures are
returned as ordinary successful results, and polling intervals are not
range-checked. See the [shared limitations](../../README.md#current-limitations).
