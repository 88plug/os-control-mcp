# os-control-mcp

The sanctioned OS **motor cortex** for an agent on a Linux box. Control the host
through its *structured* interfaces — **systemd**, **logind**, **journald**, and
the **D-Bus** buses — instead of raw `kill`/PID hacks. The system-service
counterpart to [screen-mcp](https://github.com/88plug/screen-mcp)'s GUI control.
Pure standard library, zero pip runtime deps. Linux + systemd.

## Install

```text
/plugin marketplace add 88plug/os-control-mcp
/plugin install os-control-mcp@os-control-mcp
/mcp          # confirm the "os" server loaded and tools are listed
```

Installs **disabled by default** (it can stop services and power off the box —
opt in consciously). No setup: it uses the host's existing systemd/D-Bus tooling.

## The loop

**sense → act → confirm.** `os_diag` (health/privilege) → `os_services` /
`os_journal` / `os_resources` / `os_processes` (observe) → `os_service` /
`os_power` / `os_dbus` / `os_notify` (act) → re-read to confirm.

## Tools

### Read-only
- **`os_diag`** — euid, backend binaries, system/user manager state, bus
  reachability, guard status. Run it first.
- **`os_services`** — list units (filter `type`/`state`/`pattern`) or `status` one. `scope`=system|user.
- **`os_journal`** — journald: `unit`, `lines`, `since`, `priority`, `boot`, `grep`. `scope`=system|user.
- **`os_resources`** — load, `free -h`, `df -h`, and optional per-`unit` accounting.
- **`os_processes`** — top by `by`=cpu|mem, `limit`, `pattern`.

### Mutating (guarded)
- **`os_service`** — `start|stop|restart|reload|try-restart|enable|disable|mask|unmask|kill`.
  Severing actions on protected units are refused unless `force=true` (see below).
- **`os_power`** — `suspend|hibernate|hybrid-sleep|suspend-then-hibernate|reboot|poweroff|halt`.
  Refused unless `confirm=true`.
- **`os_dbus`** — `list|tree|introspect|call`. `call` refused unless `force=true`.
- **`os_notify`** — desktop notification to the logged-in user.
- **`os_reload`** — hot-reload the server in place.

## Safety model

Three guards, by design:

| Guard | Applies to | Bypass |
|---|---|---|
| **Self-preservation** | severing systemd actions (stop/restart/disable/mask/kill) on units the agent depends on — `dbus`, `systemd-logind`, `sshd`, `NetworkManager`/networkd, `tailscaled`, `user@…` session, `goosed`, … | `force=true` |
| **Power confirm** | `os_power` (irreversible) | `confirm=true` |
| **D-Bus call** | `os_dbus op=call` (side effects) | `force=true` |

The self-preservation guard is the analog of screen-mcp's user-takeover guard —
*don't saw off the branch you're sitting on.*

## Privilege

Read-only tools work unprivileged. System-scope mutations (`os_service` on system
units, `os_power`) need root or polkit — when not root the server tries `sudo -n`
and otherwise says so. Options: run as root, passwordless sudo for `systemctl`, a
polkit rule, or use `scope="user"` for the user's own units.

## Pairs with

[screen-mcp](https://github.com/88plug/screen-mcp) (GUI), NATS (messaging), A2A
(inter-agent) — sense the kernel/services, act on the system, drive the desktop,
coordinate the fleet.
