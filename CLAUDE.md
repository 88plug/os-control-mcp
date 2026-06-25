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
- **Human-in-the-loop is the headline.** Every destructive path routes through
  `require_human(desc, flag_name, args, *, floor, headless_allow)`. Order: hard
  floor → `elicit()` (MCP `elicitation/create`, the human decides — model flags
  IGNORED) → `OSCTL_REQUIRE_HUMAN=1` refuses if no elicitation → flag fallback
  (`force`/`confirm`) / `headless_allow` for low self-risk. `ELICIT_OK` is set at
  `initialize` from the client's `capabilities.elicitation`. NOTE: `main()` uses
  `sys.stdin.readline()` (NOT `for line in sys.stdin`) so `elicit()` can read the
  human's reply without the iterator's read-ahead eating it — do not revert that.
  New destructive handlers MUST call `require_human()` (don't re-add bare flag
  checks) and pass `approval_path()` into `audit()`.
- **Self-preservation guard** (`PROTECTED_TOKENS` + `SEVERING_ACTIONS` +
  `is_protected`): a severing systemd action (stop/restart/disable/mask/kill) on a
  unit the agent stands on (dbus, logind, sshd, network, tailscaled, the session,
  goosed, …) is refused unless `force=true`. This is the analog of screen-mcp's
  user-takeover guard — "don't saw off the branch you're sitting on." When adding
  units the agent depends on, extend `PROTECTED_TOKENS`.
- **Hard floor** (`CRITICAL_FLOOR` + `is_floor`): severing the agent's *absolute*
  substrate (dbus/dbus-broker/systemd-logind/init.scope/-.slice/basic.target/
  sysinit.target) is refused **even with `force=true`** — there is no flag that
  power-cycles the bus the model speaks on. `CRITICAL_FLOOR ⊂ PROTECTED_TOKENS`
  conceptually; keep the floor tiny and truly unbypassable.
- **`os_power` requires `confirm=true`**; **`os_dbus` `set-property`/`call` and the
  `os_time`/`os_hostname`/`os_locale` writes require `force=true`**.
- **`dry_run=true`** is honored by every mutating handler (returns the exact
  command via `_cmdstr`, runs nothing) — keep it working when adding mutations.
- **Audit log**: every mutation calls `audit()` → append-only JSONL at
  `$XDG_STATE_HOME/os-control-mcp/audit.jsonl`. New mutating handlers must `audit()`.
- Read-only tools carry `readOnlyHint: True` (`_RO`); mutating tools carry
  `destructiveHint: True` (`_MUT`). Keep these honest.

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
