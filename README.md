<div align="center">

# os-control-mcp

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/88plug/os-control-mcp)

The sanctioned OS "motor cortex" for an agent on a Linux box — control systemd, logind, journald, and D-Bus through structured interfaces, never raw PID hacks.

[![plugin-validate](https://github.com/88plug/os-control-mcp/actions/workflows/plugin-validate.yml/badge.svg)](https://github.com/88plug/os-control-mcp/actions/workflows/plugin-validate.yml)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat)](LICENSE.md)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2?style=flat)](https://github.com/88plug/claude-code-plugins)
[![Docs](https://img.shields.io/badge/docs-online-2ea44f?style=flat)](https://88plug.github.io/os-control-mcp/)

</div>

os-control-mcp is an **MCP server** — usable by **any agent or MCP client**
(Claude Code, Cursor, Cline, Goose, Continue, your own MCP host, …) — that gives
a model **sanctioned control of a Linux host**: manage systemd services and
timers, query journald, read host resources and processes, send desktop
notifications, drive the D-Bus buses, and manage power — all through the host's
*structured* interfaces (`systemctl`, `loginctl`, `journalctl`, `busctl`), never
raw `kill`/PID hacks. It is the system-service counterpart to
[screen-mcp](https://github.com/88plug/screen-mcp)'s GUI control. **Pure standard
library, zero pip runtime deps.** Linux + systemd.

## Quickstart

Install the plugin in Claude Code:

```text
/plugin marketplace add 88plug/os-control-mcp
/plugin install os-control-mcp@os-control-mcp
```

Then confirm the server loaded and its tools are available:

```text
/mcp
```

No setup needed — it uses the host's existing systemd/D-Bus tooling. Run the
`os_diag` tool first; it reports your privilege level and which backends are
present.

> ⚠️ **Treat this plugin as privileged.** It can stop services and power off the
> machine. The guards below make that hard to do by accident, but install it
> deliberately and disable it via `/plugin` when you're not using it.

## Use from any MCP client

It's a plain stdio MCP server — no Claude-Code lock-in. Point any MCP client at
the launcher (or `python3 server.py` directly):

```jsonc
// e.g. Cursor / Cline / Goose / your own MCP host
{
  "mcpServers": {
    "os": { "command": "python3", "args": ["/path/to/os-control-mcp/server.py"] }
  }
}
```

It speaks MCP `2025-11-25` over stdio; the tools appear like any other MCP server.

## Tools

**Observe (read-only)**

| Tool | What |
|---|---|
| `os_diag` | health: privilege, backends, manager state, bus reachability, safety status |
| `os_services` | inspect units — `list`/`status`/`show`/`cat`/`deps`/`files` (system or user) |
| `os_journal` | journald — unit/since/until/priority/`grep` (server-side regex)/`-k` dmesg/boots/`match` |
| `os_resources` | load + memory + disk (+ optional per-unit accounting) |
| `os_processes` | top processes by cpu/mem |
| `os_pressure` | PSI from `/proc/pressure` — the real "is the box starving" signal |
| `os_net` | sockets via `ss` (listening + owning process, or summary) |
| `os_sensors` | thermal-zone temperatures (+ `lm_sensors` if present) |
| `os_session` | logind sessions / users / **inhibitors** |

**Act (guarded)**

| Tool | What | Safety |
|---|---|---|
| `os_service` | start/stop/restart/reload/enable/disable/mask/kill/**reset-failed**/**daemon-reload** (single or **batch**) | **hard floor + self-preservation guard**; `dry_run` |
| `os_wait` | block until a unit is active/inactive/failed (or timeout) | — |
| `os_power` | suspend/hibernate/reboot/poweroff/halt | **needs `confirm=true`**; `dry_run` |
| `os_time` / `os_hostname` / `os_locale` | machine settings (timezone/NTP, hostname, locale/keymap) | writes need `force=true`; `dry_run` |
| `os_dbus` | list/tree/introspect/get-property/set-property/`call` (system or session bus) | writes need `force=true`; `dry_run` |
| `os_notify` | desktop notification to the logged-in user | — |
| `os_reload` | hot-reload the server in place | — |

## The guards (the whole point)

Three layers, plus an audit trail:

1. **Hard floor (unbypassable).** A *severing* action on the agent's absolute
   substrate — `dbus`, `systemd-logind`, `init.scope`, `-.slice`, `basic.target` —
   is refused **even with `force=true`**. There is no flag that lets a model
   power-cycle the bus it's speaking on.
2. **Self-preservation guard (`force`-overridable).** A severing action
   (stop/restart/disable/mask/kill) on a unit the agent *stands on* (`sshd`,
   `NetworkManager`/networkd, `tailscaled`, the `user@…` session, `goosed`, …) is
   refused unless `force=true` — *don't saw off the branch you're sitting on.*
3. **Confirm / dry-run.** `os_power` needs `confirm=true`; machine-setting writes
   and `os_dbus` `set-property`/`call` need `force=true`; **any mutating tool
   accepts `dry_run=true`** to return the exact command without running it.

Every mutation is appended to an **audit log** at
`$XDG_STATE_HOME/os-control-mcp/audit.jsonl`.

## Privilege

Read-only tools work unprivileged. **System-scope mutations** (`os_service` on
system units, `os_power`) need root or polkit — when not root the server tries
`sudo -n` and otherwise tells you plainly. Options: run as root, add passwordless
sudo for `systemctl`, or a polkit rule. `scope="user"` manages the user's own
units with no root.

## Documentation

Full per-tool reference and the safety model live at
**[88plug.github.io/os-control-mcp](https://88plug.github.io/os-control-mcp/)**.

## Pairs with

[screen-mcp](https://github.com/88plug/screen-mcp) (GUI eyes + hands), NATS
(messaging), and A2A (inter-agent) — together: sense the kernel/services, act on
the system, drive the desktop, coordinate the fleet.

## License

[FSL-1.1-ALv2](LICENSE.md) — © 2026 88plug.
