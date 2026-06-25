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
