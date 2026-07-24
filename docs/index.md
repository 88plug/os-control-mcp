# os-control-mcp

**Linux systemd MCP** for Claude Code and Grok — control services, journald, D-Bus, and power through structured OS interfaces, never raw PID hacks.

[![Docs](https://img.shields.io/badge/docs-online-blue?style=flat)](https://88plug.github.io/os-control-mcp/)

The sanctioned OS **motor cortex** for an agent on a Linux box. Control the host
through its *structured* interfaces — **systemd**, **logind**, **journald**, and
the **D-Bus** buses — instead of raw `kill`/PID hacks. The system-service
counterpart to [screen-mcp](https://github.com/88plug/screen-mcp)'s GUI control.

**Pure standard library. Zero pip runtime deps. Linux + systemd.**

!!! warning "Privileged by design"
    This server can stop services and power off the box. Install it deliberately.
    Disable it via `/plugin` when not in use. Destructive actions are human-gated
    (see [Safety model](#safety-model-human-in-the-loop)).

## Requirements

!!! important "Linux + systemd only"
    os-control-mcp shells out to `systemctl`, `loginctl`, `journalctl`, and
    `busctl`. It targets a systemd Linux host. It will not run on macOS, Windows,
    or non-systemd Linux.

| Need | Notes |
|---|---|
| Linux + systemd | Arch, Debian/Ubuntu, Fedora, … |
| Python 3.10+ | Fleet T1 floor via `run-python.sh`; pure stdlib runtime — no pip packages required to run |
| D-Bus | System + session bus for `os_dbus` / notifications |
| Optional | `libnotify` (`notify-send`), `sudo` for non-root system mutations, Docker/Podman for `os_containers` |

## Install

### Claude Code

```text
/plugin marketplace add 88plug/claude-code-plugins
/plugin install os-control-mcp@88plug
/mcp
```

### Grok Build

```text
grok plugin marketplace add 88plug/claude-code-plugins
grok plugin install os-control-mcp@88plug --trust
```


Confirm the `os` server loaded and its tools are listed. No extra setup — it
uses the host's existing systemd/D-Bus tooling. Call **`os_diag` first**; it
reports privilege, backends, and safety status.

### Any MCP client

Plain stdio MCP (`2025-11-25`). Point any host at the launcher or `server.py`:

```json
{
  "mcpServers": {
    "os": {
      "command": "python3",
      "args": ["/path/to/os-control-mcp/server.py"]
    }
  }
}
```

## The loop: `os_diag` → observe → act → confirm

Drive the host in this order — never mutate blind:

1. **`os_diag`** — health, euid/privilege, backend binaries, manager state, bus
   reachability, HIL/gating status.
2. **Observe** — read-only tools: `os_services`, `os_journal`, `os_resources`,
   `os_processes`, `os_pressure`, `os_net`, `os_disk`, `os_hardware`,
   `os_containers`, `os_sensors`, `os_session`, `os_verify`.
3. **Act** — guarded tools: `os_service`, `os_power`, `os_dbus`, `os_time`,
   `os_hostname`, `os_locale`, `os_notify`.
4. **Confirm** — `os_wait` and/or re-read status/journal. Do not assume the
   action took.

```text
os_diag  →  observe (read-only)  →  act (HIL-gated)  →  os_wait / re-read
```

## Tools

### Observe (read-only)

| Tool | What |
|---|---|
| `os_diag` | Health first: privilege, backends, manager state, bus, safety status |
| `os_services` | Inspect units — `list` / `status` / `show` / `cat` / `deps` / `files` (`scope`=system\|user) |
| `os_journal` | journald — unit / since / until / priority / `grep` / dmesg / boots / `match` |
| `os_resources` | Load + memory + disk (+ optional per-unit accounting) |
| `os_processes` | Top processes by `cpu` or `mem` |
| `os_pressure` | PSI from `/proc/pressure` — the real "is the box starving" signal |
| `os_net` | Sockets (`ss`), `ip` addr/links/routes, wifi, NetworkManager |
| `os_disk` | `df`, `du` (largest dirs), `lsblk`, mounts |
| `os_containers` | Docker/Podman — ps / logs / inspect / stats / images / compose |
| `os_hardware` | CPU / PCI / USB / **GPU** (nvidia-smi + DRM) inventory |
| `os_sensors` | Thermal zones (+ `lm_sensors` if present) |
| `os_session` | logind sessions / users / inhibitors |
| `os_verify` | Cross-layer action check — `begin` snapshots unit state + a journald cursor → token; `end` returns CONFIRMED / PARTIAL / NO_OP / DIVERGED (fuses unit-state change, journald errors, an `expect` map, and an optional screen-mcp `pixel` signal). Read-only. |

### Act (guarded)

| Tool | What | Safety |
|---|---|---|
| `os_service` | start / stop / restart / reload / enable / disable / mask / kill / reset-failed / daemon-reload (single or **batch**) | hard floor + self-preservation; `force`; `dry_run` |
| `os_wait` | Block until a unit is active / inactive / failed (or timeout) | — |
| `os_power` | suspend / hibernate / reboot / poweroff / halt | needs **`confirm=true`**; `dry_run` |
| `os_time` | timezone / NTP (`timedatectl`) | writes need **`force=true`**; `dry_run` |
| `os_hostname` | hostname (`hostnamectl`) | writes need **`force=true`**; `dry_run` |
| `os_locale` | locale / keymap (`localectl`) | writes need **`force=true`**; `dry_run` |
| `os_dbus` | list / tree / introspect / get-property / set-property / `call` | writes need **`force=true`**; `dry_run` |
| `os_notify` | Desktop notification to the logged-in user | — |
| `os_reload` | Hot-reload the server in place | — |

## Safety model — human-in-the-loop

!!! danger "Human-in-the-loop, not model-in-the-loop"
    Destructive actions (severing a service, power transitions, D-Bus / machine
    *writes*) require a **human's** approval. The model does not self-authorize
    when elicitation is available.

| Layer | Applies to | Resolution |
|---|---|---|
| **Hard floor** | Severing absolute substrate: `dbus`, `dbus-broker`, `systemd-logind`, `init.scope`, `-.slice`, `basic.target`, `sysinit.target` | **Refused always** — even with `force` |
| **Human approval (elicitation)** | Every destructive action, when the client supports MCP elicitation | Server sends `elicitation/create`; runs only if the **human accepts**. Model `force` / `confirm` is **ignored** |
| **Flag fallback** | Same actions, when the client cannot elicit | `force` / `confirm` flags. Severing a unit the agent stands on (`sshd`, `NetworkManager`, `tailscaled`, session, `goosed`, …) still needs `force`. **`OSCTL_REQUIRE_HUMAN=1`** disables this fallback entirely |
| **Preview** | Any mutating tool | **`dry_run=true`** returns the exact command and runs nothing |

### Flags: `dry_run` / `force` / `confirm`

| Flag | Tools | Effect |
|---|---|---|
| `dry_run=true` | All mutating tools | Preview the exact command; execute nothing |
| `force=true` | `os_service` (severing protected units), `os_dbus` writes, `os_time` / `os_hostname` / `os_locale` writes | Headless override when elicitation is unavailable; **never** overrides the hard floor |
| `confirm=true` | `os_power` | Required for power transitions (same HIL rules) |

!!! warning "Severing actions"
    `stop` / `kill` / `restart` / `try-restart` / `disable` / `mask` on a unit
    the agent depends on are refused unless human-approved (or `force` on the
    flag path). The hard floor is never bypassable.

Every mutation is appended — with its approval path (`human` vs `flag`) — to
`$XDG_STATE_HOME/os-control-mcp/audit.jsonl`.

## Privilege

Read-only tools work unprivileged. **System-scope mutations** (`os_service` on
system units, `os_power`, machine settings) need root or polkit. When not root
the server tries `sudo -n` and otherwise says so.

| Option | When |
|---|---|
| Run as root | Full system control |
| Passwordless sudo for `systemctl` | Non-root agent + headless system mutations |
| Polkit rule | Desktop / interactive elevation |
| `scope="user"` | User units only — no root required |

## Principles — The Agent Oath

os-control-mcp is a reference **enforcer** of
[The Agent Oath](https://theagentoath.com): the gating is the Oath made
executable.

| Oath principle | Enforced by |
|---|---|
| §1 Human welfare over task completion | Hard floor + HIL |
| §2 Human agency | HIL elicitation |
| §3 Protect systems | Sanctioned interfaces only |
| §5 Transparency | Audit log + `dry_run` |
| §7 Don't bypass safety | Unbypassable floor + `OSCTL_REQUIRE_HUMAN` |
| §11 Respect human oversight | HIL authority + operator bounds |

The Oath is the rationale; the **operator's gating is the authority**. This
server does not adopt any "supersedes instructions" clause. `os_diag` reports
the enforced principles.

## Pairs with

[screen-mcp](https://github.com/88plug/screen-mcp) (GUI), NATS (messaging), A2A
(inter-agent) — sense the kernel/services, act on the system, drive the desktop,
coordinate the fleet.

## License

[FSL-1.1-ALv2](https://github.com/88plug/os-control-mcp/blob/main/LICENSE) —
© 2026 88plug.

## Features

| Area | What you get |
|---|---|
| **Systemd** | List/status/deps units; start/stop/restart/enable/mask/kill; batch ops; `os_wait` |
| **Journald** | Unit/since/until/priority filters, server-side `grep`, dmesg, boots |
| **Host telemetry** | Load, memory, disk, PSI pressure, processes, net, hardware, sensors, containers |
| **D-Bus + settings** | Introspect/call system or session bus; timezone, hostname, locale |
| **Power + notify** | Suspend/hibernate/reboot/poweroff; desktop notifications to the logged-in user |
| **Safety** | Hard floor, human-in-the-loop elicitation, self-preservation guard, `dry_run`, audit log |
