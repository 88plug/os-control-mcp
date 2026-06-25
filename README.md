<div align="center">

# os-control-mcp

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/88plug/os-control-mcp)

The sanctioned OS "motor cortex" for an agent on a Linux box — control systemd, logind, journald, and D-Bus through structured interfaces, never raw PID hacks.

[![plugin-validate](https://github.com/88plug/os-control-mcp/actions/workflows/plugin-validate.yml/badge.svg)](https://github.com/88plug/os-control-mcp/actions/workflows/plugin-validate.yml)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat)](LICENSE.md)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2?style=flat)](https://github.com/88plug/claude-code-plugins)
[![Docs](https://img.shields.io/badge/docs-online-2ea44f?style=flat)](https://88plug.github.io/os-control-mcp/)

</div>

os-control-mcp is an MCP server for Claude Code that gives a model **sanctioned
control of a Linux host** — manage systemd services and timers, query journald,
read host resources and processes, send desktop notifications, drive the D-Bus
buses, and manage power — all through the host's *structured* interfaces
(`systemctl`, `loginctl`, `journalctl`, `busctl`), never raw `kill`/PID hacks.
It is the system-service counterpart to
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
present. (The plugin installs **disabled by default** — it can stop services and
power off the machine, so you opt in consciously.)

## Tools

| Tool | What | Safety |
|---|---|---|
| `os_diag` | health: privilege, backends, manager state, bus reachability | read-only |
| `os_services` | list units / `status` one (filter by type/state/pattern, system or user) | read-only |
| `os_service` | start/stop/restart/reload/enable/disable/mask/kill a unit | **self-preservation guard** |
| `os_journal` | journald query (unit/since/priority/grep/boot) | read-only |
| `os_resources` | load + memory + disk (+ optional per-unit) | read-only |
| `os_processes` | top processes by cpu/mem | read-only |
| `os_power` | suspend/hibernate/reboot/poweroff/halt | **needs `confirm=true`** |
| `os_notify` | desktop notification to the logged-in user | — |
| `os_dbus` | list/tree/introspect/`call` the system or session bus | `call` needs `force=true` |
| `os_reload` | hot-reload the server in place | — |

## The guards (the whole point)

- **Self-preservation guard** — a *severing* action (stop/restart/disable/mask/kill)
  on a unit the agent depends on (`dbus`, `systemd-logind`, `sshd`,
  `NetworkManager`/networkd, `tailscaled`, the `user@…` session, `goosed`, …) is
  **refused unless `force=true`**. You can't accidentally cut off your own bus,
  login, network, or remote access — *don't saw off the branch you're sitting on.*
- **Power** is irreversible → `os_power` refuses unless `confirm=true`.
- **D-Bus `call`** has side effects → refused unless `force=true`. `list`/`tree`/
  `introspect` are free.

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
