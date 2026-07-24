"""Tests for os_verify — the cross-layer action verifier.

Two layers of coverage:
  1. Pure `_verify_reconcile` logic (deterministic, no subprocess).
  2. Hermetic handler tests (monkeypatch `server.run`).
  3. A real-systemd integration test against a transient `--user` unit, skipped
     when a user manager / systemd-run is unavailable.
"""

import json
import shutil
import subprocess
import time

import pytest

import server


# --------------------------------------------------------------------------- #
# 1. pure verdict logic
# --------------------------------------------------------------------------- #
def _snap(active, sub="running", result="success", nrestarts="0"):
    return {
        "ActiveState": active,
        "SubState": sub,
        "Result": result,
        "NRestarts": nrestarts,
    }


def test_reconcile_confirmed_on_expected_state():
    before = {"x.service": _snap("active")}
    after = {"x.service": _snap("inactive", "dead")}
    v = server._verify_reconcile(
        before, after, {"x.service": "inactive"}, {"errors": 0, "warnings": 0}, None
    )
    assert v["status"] == "CONFIRMED"
    assert v["os_changed"] is True
    assert v["units"]["x.service"]["met"] is True


def test_reconcile_no_op_when_nothing_changed():
    before = {"x.service": _snap("active")}
    after = {"x.service": _snap("active")}
    v = server._verify_reconcile(before, after, {}, {"errors": 0, "warnings": 0}, None)
    assert v["status"] == "NO_OP"
    assert v["os_changed"] is False


def test_reconcile_diverged_on_failed_unit():
    before = {"x.service": _snap("active")}
    after = {"x.service": _snap("failed", "failed", result="exit-code")}
    v = server._verify_reconcile(
        before, after, {"x.service": "active"}, {"errors": 0, "warnings": 0}, None
    )
    assert v["status"] == "DIVERGED"
    assert v["os_failed"] is True


def test_reconcile_diverged_on_journal_errors_without_expect():
    before = {"x.service": _snap("active")}
    after = {"x.service": _snap("active")}
    v = server._verify_reconcile(before, after, {}, {"errors": 3, "warnings": 0}, None)
    assert v["status"] == "DIVERGED"


def test_reconcile_partial_on_mixed_expectations():
    before = {"a.service": _snap("active"), "b.service": _snap("active")}
    after = {"a.service": _snap("inactive", "dead"), "b.service": _snap("active")}
    v = server._verify_reconcile(
        before,
        after,
        {"a.service": "inactive", "b.service": "inactive"},
        {"errors": 0, "warnings": 0},
        None,
    )
    assert v["status"] == "PARTIAL"


def test_reconcile_unmatched_expect_key_does_not_false_confirm():
    """Regression for the code-review finding (2026-07-24): an `expect` key that doesn't
    match any unit passed to `units` at begin (different spelling, or a unit never listed)
    used to be silently skipped — met_list stayed empty for the wrong reason, and unrelated
    os_changed activity on OTHER units could report a false CONFIRMED without the caller's
    actual expectation ever being checked."""
    before = {"myapp.service": _snap("active")}
    after = {
        "myapp.service": _snap("active", "running", nrestarts="1")
    }  # unrelated change
    v = server._verify_reconcile(
        before,
        after,
        {"myapp": "active"},  # spelled without .service -> never matches `before`'s key
        {"errors": 0, "warnings": 0},
        None,
    )
    assert v["status"] == "DIVERGED"
    assert v["unmatched_expect"] == ["myapp"]


def test_reconcile_unmatched_expect_key_degrades_confirmed_to_partial():
    """When some expect keys match and are met but others are unmatched, the overall
    verdict must not be a clean CONFIRMED — part of the expectation was never checked."""
    before = {"a.service": _snap("active"), "b.service": _snap("active")}
    after = {"a.service": _snap("inactive", "dead"), "b.service": _snap("active")}
    v = server._verify_reconcile(
        before,
        after,
        {"a.service": "inactive", "wrong-name": "active"},
        {"errors": 0, "warnings": 0},
        None,
    )
    assert v["status"] == "PARTIAL"
    assert v["unmatched_expect"] == ["wrong-name"]


def test_cross_layer_mismatch_flags_diverged():
    # OS layer saw nothing, but the GUI changed → invisible divergence.
    before = {"x.service": _snap("active")}
    after = {"x.service": _snap("active")}
    v = server._verify_reconcile(
        before, after, {}, {"errors": 0, "warnings": 0}, {"changed": True}
    )
    assert v["status"] == "DIVERGED"
    assert v["reconciled"] is False
    assert v["cross_layer"] == "pixel-changed-os-static"


def test_cross_layer_reconciled_when_both_agree():
    before = {"x.service": _snap("active")}
    after = {"x.service": _snap("inactive", "dead")}
    v = server._verify_reconcile(
        before,
        after,
        {"x.service": "inactive"},
        {"errors": 0, "warnings": 0},
        {"changed": True},
    )
    assert v["status"] == "CONFIRMED"
    assert v["reconciled"] is True
    assert v["cross_layer"] is None


# --------------------------------------------------------------------------- #
# 2. hermetic handler tests
# --------------------------------------------------------------------------- #
def test_verify_begin_token_roundtrips(monkeypatch):
    def fake_run(cmd, timeout=25, input_text=None):
        if "show" in cmd:
            return 0, "ActiveState=active\nSubState=running\nNRestarts=0\nResult=success\n", ""
        if "--show-cursor" in cmd:
            return 0, "-- cursor: s=abc;i=1\n", ""
        return 0, "", ""

    monkeypatch.setattr(server, "run", fake_run)
    r = server.h_verify({"action": "begin", "units": ["x.service"]})
    assert r.get("isError") is not True
    payload = json.loads(r["content"][0]["text"])
    token = json.loads(server.base64.b64decode(payload["token"]).decode())
    assert token["units"] == ["x.service"]
    assert token["cursor"] == "s=abc;i=1"
    assert token["before"]["x.service"]["ActiveState"] == "active"


def test_verify_begin_requires_units():
    r = server.h_verify({"action": "begin"})
    assert r.get("isError") is True


def test_verify_end_rejects_malformed_token():
    r = server.h_verify({"action": "end", "token": "not-base64-json"})
    assert r.get("isError") is True
    assert "malformed" in r["content"][0]["text"]


def test_verify_bad_action():
    assert server.h_verify({"action": "sideways"}).get("isError") is True


def test_verify_end_reconciles_a_stop(monkeypatch):
    # begin sees active; end sees inactive → CONFIRMED for expect=inactive.
    state = {"phase": "before"}

    def fake_run(cmd, timeout=25, input_text=None):
        if "show" in cmd:
            act = "active" if state["phase"] == "before" else "inactive"
            sub = "running" if state["phase"] == "before" else "dead"
            return 0, f"ActiveState={act}\nSubState={sub}\nNRestarts=0\nResult=success\n", ""
        if "--show-cursor" in cmd:
            return 0, "-- cursor: s=zzz;i=9\n", ""
        # journalctl since-cursor passes → no error lines
        return 0, "", ""

    monkeypatch.setattr(server, "run", fake_run)
    b = server.h_verify(
        {"action": "begin", "units": ["x.service"], "expect": {"x.service": "inactive"}}
    )
    token = json.loads(b["content"][0]["text"])["token"]
    state["phase"] = "after"
    e = server.h_verify({"action": "end", "token": token})
    verdict = json.loads(e["content"][0]["text"])
    assert verdict["status"] == "CONFIRMED"
    assert verdict["units"]["x.service"]["met"] is True


# --------------------------------------------------------------------------- #
# 3. real-systemd integration (guarded)
# --------------------------------------------------------------------------- #
def _user_systemd_ok():
    if not shutil.which("systemd-run") or not shutil.which("systemctl"):
        return False
    try:
        p = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        return False
    return p.returncode == 0 or "running" in (p.stdout + p.stderr)


requires_user_systemd = pytest.mark.skipif(
    not _user_systemd_ok(), reason="no reachable --user systemd manager"
)


@requires_user_systemd
def test_real_stop_is_confirmed():
    unit = f"osverify-it-{int(time.time())}.service"
    subprocess.run(
        ["systemd-run", "--user", f"--unit={unit}", "sleep", "3000"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    try:
        subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=8,
        )
        b = server.h_verify(
            {
                "action": "begin",
                "units": [unit],
                "expect": {unit: "inactive"},
                "scope": "user",
            }
        )
        token = json.loads(b["content"][0]["text"])["token"]
        subprocess.run(
            ["systemctl", "--user", "stop", unit],
            capture_output=True,
            text=True,
            timeout=15,
        )
        e = server.h_verify({"action": "end", "token": token})
        verdict = json.loads(e["content"][0]["text"])
        assert verdict["status"] == "CONFIRMED", verdict
        assert verdict["os_changed"] is True
    finally:
        subprocess.run(
            ["systemctl", "--user", "reset-failed", unit],
            capture_output=True,
            text=True,
            timeout=8,
        )


@requires_user_systemd
def test_real_no_op_when_untouched():
    # A unit we never touch between begin and end → NO_OP.
    unit = f"osverify-noop-{int(time.time())}.service"
    subprocess.run(
        ["systemd-run", "--user", f"--unit={unit}", "sleep", "3000"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    try:
        b = server.h_verify(
            {"action": "begin", "units": [unit], "scope": "user"}
        )
        token = json.loads(b["content"][0]["text"])["token"]
        e = server.h_verify({"action": "end", "token": token})
        verdict = json.loads(e["content"][0]["text"])
        assert verdict["status"] == "NO_OP", verdict
    finally:
        subprocess.run(
            ["systemctl", "--user", "stop", unit],
            capture_output=True,
            text=True,
            timeout=10,
        )
        subprocess.run(
            ["systemctl", "--user", "reset-failed", unit],
            capture_output=True,
            text=True,
            timeout=8,
        )
