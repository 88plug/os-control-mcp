<div align="center">

# os-control-mcp

**Linux systemd MCP** for Claude Code and Grok — control services, journald, D-Bus, and power through structured OS interfaces, never raw PID hacks.

[![plugin-validate](https://github.com/88plug/os-control-mcp/actions/workflows/plugin-validate.yml/badge.svg)](https://github.com/88plug/os-control-mcp/actions/workflows/plugin-validate.yml)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-online-2ea44f?style=flat)](https://88plug.github.io/os-control-mcp/)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2?style=flat)](https://github.com/88plug/claude-code-plugins)
[![DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/88plug/os-control-mcp)

</div>

os-control-mcp is a pure-stdlib **MCP server** and Claude Code / Grok plugin that gives any model-context-protocol client sanctioned control of a **Linux** host. Manage **systemd** services and timers, query **journald**, read host resources and processes, send desktop notifications, drive **D-Bus**, and manage power — all via `systemctl`, `loginctl`, `journalctl`, and `busctl`, never raw `kill`/PID hacks.

It is the system-service counterpart to [screen-mcp](https://github.com/88plug/screen-mcp)'s GUI control. Zero pip runtime deps. Ships the MCP server plus a **control-os** skill. Linux + systemd only.

## Install

### Claude Code

```text
/plugin marketplace add 88plug/claude-code-plugins
/plugin install os-control-mcp@88plug
```

### Grok Build

```text
grok plugin marketplace add 88plug/claude-code-plugins
grok plugin install os-control-mcp@88plug --trust
```


Confirm the server loaded:

```text
/mcp
```

No setup needed — it uses the host's existing systemd/D-Bus tooling. Call **`os_diag` first**; it reports privilege, backends, and safety status.

> [!WARNING]
> Treat this plugin as privileged. It can stop services and power off the machine. Guards make accidents hard, but install deliberately and disable via `/plugin` when not in use.

## Features

| Area | What you get |
|---|---|
| **Systemd** | List/status/deps units; start/stop/restart/enable/mask/kill; batch ops; `os_wait` |
| **Journald** | Unit/since/until/priority filters, server-side `grep`, dmesg, boots |
| **Host telemetry** | Load, memory, disk, PSI pressure, processes, net, hardware, sensors, containers |
| **D-Bus + settings** | Introspect/call system or session bus; timezone, hostname, locale |
| **Power + notify** | Suspend/hibernate/reboot/poweroff; desktop notifications to the logged-in user |
| **Safety** | Hard floor, human-in-the-loop elicitation, self-preservation guard, `dry_run`, audit log |

## Requirements

| Need | Notes |
|---|---|
| Linux + systemd | Arch, Debian/Ubuntu, Fedora, … |
| Python 3.10+ | Pure stdlib runtime — no pip packages to run |
| D-Bus | System + session for `os_dbus` / notifications |
| Optional | `libnotify` (`notify-send`), `sudo` for non-root system mutations, Docker/Podman for `os_containers` |

## Use from any MCP client

Plain stdio MCP server — no Claude Code lock-in. Point any client at the launcher or `python3 server.py`:

```jsonc
// e.g. Cursor / Cline / Goose / your own MCP host
{
  "mcpServers": {
    "os": { "command": "python3", "args": ["/path/to/os-control-mcp/server.py"] }
  }
}
```

Speaks MCP `2025-11-25` over stdio; tools appear like any other MCP server.

## The loop: `os_diag` → observe → act → confirm

```text
os_diag  →  observe (read-only)  →  act (HIL-gated)  →  os_wait / re-read
```

1. **`os_diag`** — health, privilege, backends, HIL/gating status
2. **Observe** — `os_services` / `os_journal` / `os_resources` / `os_processes` / `os_pressure` / `os_net` / `os_disk` / `os_hardware` / `os_containers` / `os_sensors` / `os_session`
3. **Act** — `os_service` / `os_power` / `os_dbus` / `os_time` / `os_hostname` / `os_locale` / `os_notify`
4. **Confirm** — `os_wait` and/or re-read status/journal

## Tools

### Observe (read-only)

| Tool | What |
|---|---|
| `os_diag` | Health: privilege, backends, manager state, bus reachability, safety status |
| `os_services` | Units — `list`/`status`/`show`/`cat`/`deps`/`files` (system or user) |
| `os_journal` | Journald — unit/since/until/priority/`grep` (server-side regex)/`-k` dmesg/boots/`match` |
| `os_resources` | Load + memory + disk (+ optional per-unit accounting) |
| `os_processes` | Top processes by cpu/mem |
| `os_pressure` | PSI from `/proc/pressure` — the real "is the box starving" signal |
| `os_net` | Sockets (`ss`), `ip` addr/links/routes, wifi, NetworkManager |
| `os_disk` | `df`, `du` (largest dirs), `lsblk`, mounts |
| `os_containers` | Docker/Podman — ps/logs/inspect/stats/images/compose |
| `os_hardware` | cpu/pci/usb/**gpu** (nvidia-smi + DRM) inventory |
| `os_sensors` | Thermal-zone temperatures (+ `lm_sensors` if present) |
| `os_session` | Logind sessions / users / **inhibitors** |
| `os_verify` | **Cross-layer action check.** `begin` snapshots unit state + a journald cursor → opaque token; do the action; `end` returns **CONFIRMED / PARTIAL / NO_OP / DIVERGED**, fusing unit-state change, journald errors, an `expect` map, and an optional screen-mcp `pixel` signal. Catches a no-op that looks like success, and a GUI that moved while the service didn't. |

### Act (guarded)

| Tool | What | Safety |
|---|---|---|
| `os_service` | start/stop/restart/reload/enable/disable/mask/kill/**reset-failed**/**daemon-reload** (single or **batch**) | **Hard floor + self-preservation guard**; `force`; `dry_run` |
| `os_wait` | Block until a unit is active/inactive/failed (or timeout) | — |
| `os_power` | suspend/hibernate/reboot/poweroff/halt | Needs **`confirm=true`**; `dry_run` |
| `os_time` / `os_hostname` / `os_locale` | Timezone/NTP, hostname, locale/keymap | Writes need `force=true`; `dry_run` |
| `os_dbus` | list/tree/introspect/get-property/set-property/`call` (system or session) | Writes need `force=true`; `dry_run` |
| `os_notify` | Desktop notification to the logged-in user | — |
| `os_reload` | Hot-reload the server in place | — |

## The guards (the whole point)

**Human-in-the-loop, not model-in-the-loop.** Every destructive action — severing a service (stop/kill/restart/disable/mask), power (reboot/poweroff/…), and D-Bus / machine-setting *writes* — is gated for a **human's** approval, in this order:

1. **Hard floor (never bypassable).** Severing the agent's absolute substrate — `dbus`, `dbus-broker`, `systemd-logind`, `init.scope`, `-.slice`, `basic.target`, `sysinit.target` — is refused **even with `force`**. No flag lets a model power-cycle the bus it's speaking on.
2. **Human approval via MCP elicitation.** When your MCP client supports elicitation, the server **asks the human** (`elicitation/create`) before any destructive action and runs it only if the human accepts. The model's `force`/`confirm` flags are **ignored** here — *the human is the authority, not the model.* (Verified: a declined elicitation never executes.)
3. **Flag fallback (only when there's no human channel).** If the client can't elicit, the server falls back to `force`/`confirm` so headless automation still works — except severing a unit the agent *stands on* (`sshd`, `NetworkManager`, `tailscaled`, the session, `goosed`, …) which still needs `force` (*don't saw off the branch you're sitting on*). Set **`OSCTL_REQUIRE_HUMAN=1`** to forbid the flag fallback entirely — no human elicitation channel, no mutation.
4. **Preview.** Any mutating tool accepts **`dry_run=true`** to return the exact command without running it.

| Flag | Tools | Effect |
|---|---|---|
| `dry_run=true` | All mutating tools | Preview the exact command; execute nothing |
| `force=true` | `os_service` (severing protected units), `os_dbus` writes, machine settings | Headless override when elicitation is unavailable; **never** overrides the hard floor |
| `confirm=true` | `os_power` | Required for power transitions (same HIL rules) |

Every mutation is appended to an **audit log** (with the approval path — human vs flag) at `$XDG_STATE_HOME/os-control-mcp/audit.jsonl`.

## Principles — The Agent Oath

os-control-mcp is a reference **enforcer** of [The Agent Oath](https://theagentoath.com) ([88plug/theagentoath.com](https://github.com/88plug/theagentoath.com)): the gating above isn't just safety plumbing, it's the Oath made executable.

| Oath principle | Enforced by |
|---|---|
| §1 Human welfare **over task completion** | Hard floor + HIL — won't sever the bus or power off the box to "finish" |
| §2 Preserve **human agency**, be transparent | HIL elicitation — the human decides; `os_diag` announces what it is |
| §3 Protect systems & data | Sanctioned interfaces only (`systemctl`/`busctl`/…), never raw PID hacks; reads default |
| §5 Transparency & accountability | Append-only audit log + `dry_run` + explicit, reasoned refusals |
| §7 Continuous vigilance, **don't bypass safety** | Unbypassable hard floor + `OSCTL_REQUIRE_HUMAN=1` |
| §11 Respect **human oversight**, don't self-modify | HIL is the authority + protected tokens + operator-defined bounds |

The Oath is the *rationale*; the **operator's gating is the authority**. This server deliberately does **not** adopt any "supersedes conflicting instructions" clause — overriding an operator's safety controls with an external document is exactly what §3 and §11 warn against. `os_diag` reports the enforced principles.

## Privilege

Read-only tools work unprivileged. **System-scope mutations** (`os_service` on system units, `os_power`) need root or polkit — when not root the server tries `sudo -n` and otherwise tells you plainly. Options: run as root, add passwordless sudo for `systemctl`, or a polkit rule. `scope="user"` manages the user's own units with no root.

## Documentation

Full per-tool reference and the safety model live at **[88plug.github.io/os-control-mcp](https://88plug.github.io/os-control-mcp/)**.

## Pairs with

[screen-mcp](https://github.com/88plug/screen-mcp) (GUI eyes + hands), NATS (messaging), and A2A (inter-agent) — together: sense the kernel/services, act on the system, drive the desktop, coordinate the fleet.

## License

[FSL-1.1-ALv2](LICENSE) — © 2026 88plug.
