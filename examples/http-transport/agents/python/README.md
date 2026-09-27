# Python agent

HTTP polling example for Python 3.9+ on Windows, Linux, or macOS. Install
[uv](https://docs.astral.sh/uv/getting-started/installation/) and follow the
[controller setup](../../README.md#quick-start) first.

From the repository root, replacing the placeholders with the controller's
HTTP address and port:

```sh
uv run examples/http-transport/agents/python/agent.py <controller_host> <controller_port>
```

uv resolves the script's `requests` dependency automatically.

The agent supports `my_what`, `my_sleep`, `my_terminal`, and `my_eval`.
`my_eval` returns the string form of a Python expression's value; printed
output is not captured as its result. The additional experimental `my_spawn`
implementation is Windows-specific.

Terminal commands have a 30-second subprocess timeout, but child-process trees
are not explicitly managed. Nonzero exit codes are currently reported as
successful results. Poll intervals are not range-checked. See the
[shared limitations](../../README.md#current-limitations) for delivery and
reconnection behavior.
