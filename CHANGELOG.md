# Changelog

## 2026.6.25

- Initial release: MCP server that gives a model sanctioned control of a Linux
  host through systemd, logind, journald, and D-Bus — `os_diag`, `os_services`,
  `os_service`, `os_journal`, `os_resources`, `os_processes`, `os_power`,
  `os_notify`, `os_dbus`, `os_reload`. Pure standard library, zero pip runtime
  deps (shells out to systemctl/loginctl/journalctl/busctl/notify-send/gdbus).
  Self-preservation guard refuses severing actions on units the agent depends on
  unless `force=true`; `os_power` requires `confirm=true`; `os_dbus` call requires
  `force=true`. Ships the `control-os` skill (sense → act → confirm loop).
