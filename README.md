# os-control-mcp

**The sanctioned OS "motor cortex" for an agent on a Linux box.** Control the host
through its *structured* interfaces — **systemd** (services/timers/resources),
**logind** (power), **journald** (logs), and the **D-Bus** buses — instead of raw
`kill`/PID hacks. It's the system-service counterpart to
[screen-mcp](https://github.com/88plug/screen-mcp)'s GUI control.

Pure standard library. **Zero pip runtime deps** — it shells out to the tooling
that *is* the sanctioned interface (`systemctl`, `loginctl`, `journalctl`,
`busctl`, `notify-send`/`gdbus`). Linux + systemd only.

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

## Install

Add the 88plug marketplace and install:

```
/plugin marketplace add 88plug/os-control-mcp
/plugin install os-control-mcp
```

or run it directly for development:

```
claude --plugin-dir ./os-control-mcp
```

No setup needed — it uses the host's existing systemd/D-Bus tooling. Run
`os_diag` to see what's available.

## Pairs with

**screen-mcp** (GUI eyes + hands), **NATS** (messaging), **A2A** (inter-agent) —
together: sense the kernel/services, act on the system, drive the desktop,
coordinate the fleet.

## License

[FSL-1.1-ALv2](LICENSE.md) — © 2026 88plug.
