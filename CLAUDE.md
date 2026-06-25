# os-control-mcp — maintainer / agent notes

The sanctioned OS "motor cortex": drive the Linux host through systemd, logind,
journald, and D-Bus — never raw PID hacks. Sibling to screen-mcp (GUI control).

## Architecture
- `server.py` — single-file stdio MCP server (raw JSON-RPC, no framework). `TOOLS`
  is the catalog; `HANDLERS` maps name → `h_*(args)`; `main()` is the stdin loop
  (`initialize` / `tools/list` / `tools/call`). Mirrors screen-mcp's framing.
- **Pure standard library.** Every capability shells out via `run()` to the host's
  own sanctioned tooling: `systemctl`, `loginctl`, `journalctl`, `busctl`,
  `notify-send`/`gdbus`. No pip runtime deps. Add a tool = add a `TOOLS` entry +
  an `h_*` handler + a `HANDLERS` key.
- `bin/os-control-mcp` — launcher (venv → system python3 → exec server.py).

## Safety (the point of the plugin — do not weaken without thought)
- **Self-preservation guard** (`PROTECTED_TOKENS` + `SEVERING_ACTIONS` +
  `is_protected`): a severing systemd action (stop/restart/disable/mask/kill) on a
  unit the agent stands on (dbus, logind, sshd, network, tailscaled, the session,
  goosed, …) is refused unless `force=true`. This is the analog of screen-mcp's
  user-takeover guard — "don't saw off the branch you're sitting on." When adding
  units the agent depends on, extend `PROTECTED_TOKENS`.
- **`os_power` requires `confirm=true`**; **`os_dbus op=call` requires `force=true`**.
- Read-only tools (`os_diag`, `os_services` list/status, `os_journal`,
  `os_resources`, `os_processes`) carry `readOnlyHint: True`; mutating tools carry
  `destructiveHint: True`. Keep these honest.

## Privilege model
Read-only works unprivileged. System-scope mutations need root/polkit; when
`euid != 0` the server prefixes `sudo -n` and, failing that, returns a clear
"needs root/polkit" message. `scope="user"` needs no root.

## Ops
- `os_diag` first when anything misbehaves — euid, backend binaries, manager
  state, bus reachability, guard status.
- Tool crashes write the full traceback to `$XDG_STATE_HOME/os-control-mcp/err.log`
  (the JSON-RPC error only carries the message).
- `os_reload` execv-reloads in place (new tools land with no /mcp reconnect).

## Tests
`pytest -q` (or run the asserts directly). Tests are hermetic — they exercise the
guards/catalog before any subprocess, so no live systemd/D-Bus is required.
Never let a change land that lets a severing action hit a protected unit without
`force`, or `os_power` fire without `confirm`.
