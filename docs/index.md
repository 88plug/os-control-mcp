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

⚠️ **Treat as privileged** — it can stop services and power off the box. Install
it deliberately and disable it via `/plugin` when not in use. No setup: it uses
the host's existing systemd/D-Bus tooling.

## The loop

**sense → act → confirm.** `os_diag` (health/privilege) →
`os_services`/`os_journal`/`os_resources`/`os_processes`/`os_pressure`/`os_net`/`os_sensors`/`os_session`
(observe) →
`os_service`/`os_power`/`os_dbus`/`os_time`/`os_hostname`/`os_locale`/`os_notify`
(act) → `os_wait` or re-read to confirm.

## Tools

### Observe (read-only)
- **`os_diag`** — euid, backend binaries, system/user manager state, bus
  reachability, safety status. Run it first.
- **`os_services`** — `op`=`list` (filter `type`/`state`/`pattern`) · `status` ·
  `show` (key=value props) · `cat` (merged unit + drop-ins) · `deps`
  (`list-dependencies`, `reverse` for reverse) · `files` (`list-unit-files`).
  `scope`=system|user.
- **`os_journal`** — journald: `unit`, `lines`, `since`/`until`, `priority`,
  `grep` (server-side PCRE), `kernel` (`-k` dmesg), `boot`, `boots`
  (`--list-boots`), `fields` (`-N`), `match`=`['_SYSTEMD_UNIT=x', …]`,
  `output`=short/json/json-pretty/cat. `scope`=system|user.
- **`os_resources`** — load, `free -h`, `df -h`, optional per-`unit` accounting.
- **`os_processes`** — top by `by`=cpu|mem, `limit`, `pattern`.
- **`os_pressure`** — PSI from `/proc/pressure/{cpu,memory,io}` (some/full avg10/60/300).
- **`os_net`** — `ss`: listening sockets + owning process; `summary=true` → `ss -s`.
- **`os_sensors`** — `/sys/class/thermal` temps; `full=true` also runs `sensors`.
- **`os_session`** — logind `list-sessions`/`list-users`/`session-status`/`inhibitors`.

### Act (guarded)
- **`os_service`** — `start|stop|restart|reload|try-restart|enable|disable|mask|`
  `unmask|kill|reset-failed|revert|daemon-reload|daemon-reexec`. `unit` may be a
  **list** (batch); omit for daemon-reload/reexec. Severing a protected unit needs
  `force=true`; the hard floor is never bypassable (see below). `dry_run=true` previews.
- **`os_wait`** — block until `unit` reaches `state` (active|inactive|failed) or `timeout`.
- **`os_power`** — `suspend|hibernate|hybrid-sleep|suspend-then-hibernate|reboot|poweroff|halt`.
  Refused unless `confirm=true` (the refusal includes the `systemctl can-*` probe). `dry_run`.
- **`os_time`** — `status`/`list-timezones`/`set-timezone`/`set-ntp`. Writes need `force=true`.
- **`os_hostname`** — `status`/`set-hostname`. Writes need `force=true`.
- **`os_locale`** — `status`/`list-locales`/`set-locale`/`set-keymap`. Writes need `force=true`.
- **`os_dbus`** — `list|tree|introspect|get-property|set-property|call`, `scope`,
  `timeout`. `set-property`/`call` need `force=true`; reads are free. `dry_run`.
- **`os_notify`** — desktop notification to the logged-in user.
- **`os_reload`** — hot-reload the server in place.

## Safety model

Three layers + an audit trail, by design:

| Layer | Applies to | Bypass |
|---|---|---|
| **Hard floor** | severing the agent's absolute substrate — `dbus`, `dbus-broker`, `systemd-logind`, `init.scope`, `-.slice`, `basic.target`, `sysinit.target` | **none** (refused even with `force`) |
| **Self-preservation** | severing systemd actions on units the agent depends on — `sshd`, `NetworkManager`/networkd, `tailscaled`, `user@…` session, `goosed`, … | `force=true` |
| **Power confirm** | `os_power` (irreversible) | `confirm=true` |
| **Writes** | `os_dbus set-property`/`call`, `os_time`/`os_hostname`/`os_locale` writes | `force=true` |
| **Preview** | any mutating tool | `dry_run=true` returns the exact command, runs nothing |

The self-preservation guard is the analog of screen-mcp's user-takeover guard —
*don't saw off the branch you're sitting on.* Every mutation is appended to
`$XDG_STATE_HOME/os-control-mcp/audit.jsonl`.

## Privilege

Read-only tools work unprivileged. System-scope mutations (`os_service` on system
units, `os_power`) need root or polkit — when not root the server tries `sudo -n`
and otherwise says so. Options: run as root, passwordless sudo for `systemctl`, a
polkit rule, or use `scope="user"` for the user's own units.

## Pairs with

[screen-mcp](https://github.com/88plug/screen-mcp) (GUI), NATS (messaging), A2A
(inter-agent) — sense the kernel/services, act on the system, drive the desktop,
coordinate the fleet.
