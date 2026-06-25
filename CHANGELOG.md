# Changelog

## 2026.6.25

Human-in-the-loop gating + data-driven observe tools (18 → 21 tools).

- **Human-in-the-loop approval.** Destructive actions (severing a service, power,
  D-Bus/machine writes) now ask a **human** via MCP **elicitation**
  (`elicitation/create`) when the client supports it — and run only if the human
  accepts. The model's `force`/`confirm` flags are **ignored** when a human channel
  exists (the human is the authority, not the model); they remain the fallback only
  for clients without elicitation. New `OSCTL_REQUIRE_HUMAN=1` forbids the flag
  fallback entirely (no elicitation → no mutation). The hard floor stays
  unbypassable. Audit log now records the approval path (human vs flag).
- **New read-only observe tools**, chosen by mining real Claude Code + opencode
  session logs for the OS-observation commands the AI actually ran:
  - **`os_containers`** — Docker/Podman ps/logs/inspect/stats/images/compose (the
    single most-used host observation in the logs by a wide margin).
  - **`os_disk`** — `df` usage, `du` (largest dirs, sorted), `lsblk`, mounts.
  - **`os_hardware`** — cpu/pci/usb + **gpu** (nvidia-smi + PCI display + DRM).
  - **`os_net`** expanded beyond sockets: `ip` addr/links/routes, wifi, NetworkManager.
- Fix: `os_time`/`os_locale` `list-*` ops are reads and are no longer gated.

## 2026.6.24

Capability + safety expansion (10 → 18 tools).

- **Safety, three layers + audit.** Added a hardcoded **floor** that refuses
  severing the agent's absolute substrate (`dbus`, `systemd-logind`, `init.scope`,
  `-.slice`, `basic.target`, `sysinit.target`) **even with `force=true`** — the
  self-preservation guard remains `force`-overridable for units the agent merely
  depends on. Every mutating tool now accepts `dry_run=true` (returns the exact
  command, runs nothing). Every mutation is appended to an audit log at
  `$XDG_STATE_HOME/os-control-mcp/audit.jsonl`.
- **systemd.** `os_service` adds `reset-failed`, `revert`, `daemon-reload`,
  `daemon-reexec` (no `unit` needed) and accepts a **list of units** (batch).
  `os_services` adds `op`: `show`, `cat`, `deps` (`list-dependencies`, `reverse`),
  `files` (`list-unit-files`). New `os_wait` blocks until a unit reaches
  active/inactive/failed or times out.
- **journald.** `os_journal` adds server-side `grep` (PCRE), `-k` kernel/dmesg,
  `until`, `boots` (`--list-boots`), `fields` (`-N`), arbitrary `match`
  (`FIELD=value`), and `output` (short/json/json-pretty/cat). No longer forces
  `sudo` — reads what the user's groups permit.
- **D-Bus.** `os_dbus` adds `get-property`, `set-property`, and `timeout`;
  `set-property`/`call` require `force=true`.
- **New machine-settings tools** (writes need `force=true`): `os_time`
  (timedatectl), `os_hostname` (hostnamectl), `os_locale` (localectl).
- **New sensing tools** (read-only): `os_pressure` (PSI from `/proc/pressure`),
  `os_net` (sockets via `ss`), `os_sensors` (thermal zones + lm_sensors),
  `os_session` (logind sessions/users/inhibitors).
- `os_power` refusal now includes the `systemctl can-*` capability probe.
- Docs site (MkDocs Material → 88plug.github.io/os-control-mcp), badge README,
  and an "any MCP client" usage section (it's a plain stdio MCP server — not
  Claude-Code-specific).

## 2026.6.23

- Initial release: MCP server that gives a model sanctioned control of a Linux
  host through systemd, logind, journald, and D-Bus — `os_diag`, `os_services`,
  `os_service`, `os_journal`, `os_resources`, `os_processes`, `os_power`,
  `os_notify`, `os_dbus`, `os_reload`. Pure standard library, zero pip runtime
  deps (shells out to systemctl/loginctl/journalctl/busctl/notify-send/gdbus).
  Self-preservation guard refuses severing actions on units the agent depends on
  unless `force=true`; `os_power` requires `confirm=true`; `os_dbus` call requires
  `force=true`. Ships the `control-os` skill (sense → act → confirm loop).
