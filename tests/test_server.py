"""Hermetic tests — the safety guards and the catalog are exercised WITHOUT touching
real systemd/D-Bus (the guard/confirm checks run before any subprocess call)."""
import server


def test_catalog_well_formed():
    names = set()
    for t in server.TOOLS:
        assert t["name"] and t["name"] not in names, f"dup/empty tool {t}"
        names.add(t["name"])
        assert t["description"] and len(t["description"]) > 30
        assert t["inputSchema"]["type"] == "object"
        assert "annotations" in t
    # every advertised tool has a handler, and vice-versa
    assert names == set(server.HANDLERS), (names ^ set(server.HANDLERS))


def test_self_preservation_guard_matches_dependencies():
    for unit in ("dbus.service", "systemd-logind.service", "sshd.service",
                 "NetworkManager.service", "tailscaled.service", "user@1000.service",
                 "goosed.service"):
        assert server.is_protected(unit), unit
    for unit in ("nginx.service", "postgresql.service", "my-app.service"):
        assert not server.is_protected(unit), unit


def test_service_refuses_severing_protected_without_force():
    r = server.h_service({"unit": "dbus.service", "action": "stop"})
    assert r.get("isError") is True
    assert "REFUSED" in r["content"][0]["text"]


def test_service_allows_nonsevering_on_protected():
    # 'reload' is not a severing action, so the guard must NOT block it
    # (it will then try systemctl; we only assert the guard didn't refuse).
    r = server.h_service({"unit": "dbus.service", "action": "reload"})
    assert "self-preservation guard" not in r["content"][0]["text"]


def test_service_requires_unit_and_action():
    assert server.h_service({"action": "stop"}).get("isError")
    assert server.h_service({"unit": "x.service"}).get("isError")


def test_power_requires_confirm():
    r = server.h_power({"action": "reboot"})
    assert r.get("isError") is True
    assert "confirm=true" in r["content"][0]["text"]


def test_power_rejects_unknown_action():
    assert server.h_power({"action": "explode", "confirm": True}).get("isError")


def test_dbus_call_requires_force():
    r = server.h_dbus({"op": "call", "service": "s", "path": "/p",
                       "interface": "i", "member": "m"})
    assert r.get("isError") is True
    assert "force=true" in r["content"][0]["text"]


# --- v0.3: hard floor, dry-run, batch, new-tool guards --------------------- #

def test_hard_floor_is_unbypassable_even_with_force():
    for unit in server.CRITICAL_FLOOR:
        r = server.h_service({"unit": unit, "action": "stop", "force": True})
        assert r.get("isError") is True, unit
        assert "hard floor" in r["content"][0]["text"].lower(), unit


def test_floor_vs_protected_are_distinct():
    assert server.is_floor("dbus.service")
    assert not server.is_floor("sshd.service")        # protected, not floor
    assert server.is_protected("sshd.service")


def test_dry_run_never_executes():
    r = server.h_service({"unit": "nginx.service", "action": "restart", "dry_run": True})
    assert not r.get("isError")
    assert "DRY RUN" in r["content"][0]["text"]
    assert "systemctl" in r["content"][0]["text"]
    rp = server.h_power({"action": "poweroff", "dry_run": True})
    assert not rp.get("isError") and "DRY RUN" in rp["content"][0]["text"]


def test_batch_units_guard_each():
    r = server.h_service({"unit": ["nginx.service", "sshd.service"], "action": "stop"})
    assert r.get("isError") and "REFUSED" in r["content"][0]["text"]


def test_daemon_reload_needs_no_unit():
    r = server.h_service({"action": "daemon-reload", "dry_run": True})
    assert not r.get("isError")
    assert "daemon-reload" in r["content"][0]["text"]


def test_machine_writes_need_force():
    r = server.h_time({"op": "set-timezone", "value": "UTC"})
    assert r.get("isError") and "force=true" in r["content"][0]["text"]
    r2 = server.h_hostname({"op": "set-hostname", "value": "x", "dry_run": True})
    assert not r2.get("isError") and "DRY RUN" in r2["content"][0]["text"]


def test_dbus_set_property_needs_force():
    r = server.h_dbus({"op": "set-property", "service": "s", "path": "/p",
                       "interface": "i", "member": "m"})
    assert r.get("isError") and "force=true" in r["content"][0]["text"]


def test_wait_validates_state():
    assert server.h_wait({"unit": "x.service", "state": "bogus"}).get("isError")
    assert server.h_wait({"state": "active"}).get("isError")
