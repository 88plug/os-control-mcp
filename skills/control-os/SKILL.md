---
name: control-os
description: >-
  Control and inspect the local Linux host through systemd, journald, logind, and D-Bus via the os-control MCP. Use this whenever the user wants to manage or diagnose the machine itself rather than files or GUIs: "restart/stop/start/enable the X service", "is X running / why did it crash", "show me the logs for X / what's in the journal", "what's eating CPU/RAM/disk", "list failed units", "reboot/suspend/power off the box", "notify me on the desktop when Y", "what's on the system D-Bus". Prefer it over raw `kill`/ad-hoc shell for service and power management — it uses the sanctioned interfaces (systemctl/loginctl/journalctl/busctl) and has guards that stop you from cutting off your own access. NOT for editing files, GUI apps (use screen-mcp), or remote hosts.
---

# Controlling the OS with os-control-mcp

The sanctioned "motor cortex" for the host: act through systemd / logind / journald / D-Bus, not raw PID hacks.

**Full tool inventory (21):**
- **Sense:** `os_diag`, `os_services`, `os_journal`, `os_resources`, `os_processes`, `os_pressure`, `os_net`, `os_disk`, `os_hardware`, `os_containers`, `os_sensors`, `os_session`
- **Act:** `os_service`, `os_power`, `os_dbus`, `os_time`, `os_hostname`, `os_locale`, `os_notify`
- **Confirm / meta:** `os_wait`, `os_reload`

## The loop: sense → act → confirm

1. **Sense first.** `os_diag` (health, privilege, backends) → observe with `os_services` / `os_journal` / `os_resources` / `os_processes` / `os_pressure` / `os_net` / `os_disk` / `os_hardware` / `os_containers` / `os_sensors` / `os_session`. Understand state before changing it.
2. **Act through the sanctioned tool.** `os_service` (start/stop/restart/enable/…), `os_power` (suspend/reboot/poweroff), `os_dbus` / `os_time` / `os_hostname` / `os_locale` / `os_notify`.
3. **Confirm.** `os_wait` and/or re-run `os_services unit=…` / `os_journal unit=…` — don't assume the action took.

## The two guards (and how to pass them)

- **Self-preservation guard.** A *severing* action (stop/restart/disable/mask/kill) on a unit the agent depends on — `dbus`, `systemd-logind`, `sshd`, `NetworkManager`/networkd, `tailscaled`, the `user@…` session, `goosed`, etc. — is **REFUSED**. This stops you cutting off your own bus / login / network / remote access. If you genuinely mean it, pass `force=true`. (start/enable/reload are never blocked.)
- **Power confirm.** `os_power` is irreversible/disruptive → **refused unless `confirm=true`**. State what you're about to do, then call with `confirm=true`.
- **D-Bus call.** `os_dbus op=call` has side effects → needs `force=true`. `list`/`tree`/`introspect` are free.

## Privilege

Read-only tools work unprivileged. **System-scope mutations** (`os_service` on system units, `os_power`) need root or polkit — when not root the server tries `sudo -n`; if that's not set up it tells you plainly. Options: run goosed/the server as root, add passwordless sudo for `systemctl`, or a polkit rule. `scope="user"` manages the user's own units with no root.

## Patterns

- **Diagnose a crash:** `os_services unit=foo.service` (state/last result) → `os_journal unit=foo.service priority=err lines=80` → fix → `os_service unit=foo.service action=restart` → confirm.
- **Find the hog:** `os_resources` → `os_processes by=cpu` (or `by=mem`) → act.
- **Health sweep:** `os_services state=failed` to list everything broken at once.
- **Reach the human:** `os_notify summary="build done" urgency=normal`.

Pairs with **screen-mcp** (GUI eyes+hands), **NATS** (messaging), and **A2A** (inter-agent) for a full-stack agent: sense the kernel/services, act on the system, drive the desktop, coordinate the fleet.
