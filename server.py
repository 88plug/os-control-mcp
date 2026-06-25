#!/usr/bin/env python3
"""os-control-mcp — the sanctioned OS "motor cortex" for an agent on a Linux box.

A thin MCP stdio server (works with ANY MCP client/agent — Claude Code, Cursor,
Cline, Goose, your own host) that drives the host through its STRUCTURED,
sanctioned interfaces — systemd (services/timers/resources), logind (power +
sessions), journald (logs), the D-Bus buses, and the machine settings
(time/hostname/locale) — instead of raw kill/PID hacks. The service-and-system
counterpart to screen-mcp's GUI motor cortex.

Design:
  * Pure standard library. No pip deps. Every capability shells out to the tools
    that already define the sanctioned interface: systemctl, loginctl, journalctl,
    busctl, timedatectl/hostnamectl/localectl, ss, notify-send/gdbus. Absent tools
    degrade with a clear message (os_diag reports availability).
  * Read-first. Listing/status/journal/resources/sensing are read-only.
  * THREE-LAYER safety:
      1. Hardcoded floor (CRITICAL_FLOOR): severing the agent's absolute substrate
         (dbus / systemd-logind / init.scope / -.slice) is refused even with force.
      2. Self-preservation guard (PROTECTED_TOKENS): severing a unit the agent
         stands on (sshd, network, tailscaled, session, goosed, …) is refused
         unless force=true.
      3. Confirm/dry-run: os_power needs confirm=true; any mutating tool accepts
         dry_run=true to return the exact command without running it; os_dbus call
         and machine-setting writes need force=true.
  * Append-only audit log of every mutating call at
    $XDG_STATE_HOME/os-control-mcp/audit.jsonl.
  * scope=system|user wherever systemd applies; system mutations use sudo -n when
    euid != 0 (or polkit), and say so plainly when neither is available.
"""
import sys, os, json, time, shutil, subprocess, shlex, glob

__version__ = "2026.6.24"

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    d = os.path.join(base, "os-control-mcp")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d

def log(msg):
    try:
        with open(os.path.join(_state_dir(), "err.log"), "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except Exception:
        pass

def audit(tool, args, outcome):
    """Append-only record of every mutating call."""
    try:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": tool,
               "args": args, "outcome": outcome[:300]}
        with open(os.path.join(_state_dir(), "audit.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass

def _txt(s):
    return {"type": "text", "text": s}

def ok(s):
    return {"content": [_txt(s)]}

def err(s):
    return {"content": [_txt(s)], "isError": True}

def have(b):
    return shutil.which(b) is not None

def run(cmd, timeout=25, input_text=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           input=input_text)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s: {' '.join(shlex.quote(c) for c in cmd)}"

def _cmdstr(cmd):
    return " ".join(shlex.quote(c) for c in cmd)

def _trunc(s, n=8000):
    return s if len(s) <= n else s[:n] + f"\n… (truncated; {len(s) - n} more chars — narrow your query)"

def _priv_prefix(binary, privileged):
    """Prefix for a privileged system command when not root."""
    if privileged and os.geteuid() != 0:
        if have("sudo"):
            return ["sudo", "-n", binary], None
        return [binary], f"{binary}: needs root and `sudo` is not installed"
    return [binary], None

def _priv_hint(e):
    if "password is required" in e or "a password is required" in e or "sudo:" in e:
        return "\n(needs root: run as root, configure passwordless sudo, or a polkit rule)"
    if "Interactive authentication required" in e:
        return "\n(polkit wants interactive auth — unavailable headless; run as root or add a polkit rule)"
    return ""

# --------------------------------------------------------------------------- #
# safety layers
# --------------------------------------------------------------------------- #
# Layer 1 — absolute floor: severing these is refused EVEN with force.
CRITICAL_FLOOR = ("dbus.service", "dbus-broker.service", "systemd-logind.service",
                  "init.scope", "-.slice", "basic.target", "sysinit.target")
# Layer 2 — force-overridable: units the agent stands on.
PROTECTED_TOKENS = (
    "dbus", "dbus-broker", "systemd-logind", "systemd-journald", "systemd-udevd",
    "systemd-resolved", "systemd-networkd", "NetworkManager", "wpa_supplicant",
    "iwd", "systemd-user-sessions", "user@", "user-runtime-dir",
    "ssh", "sshd", "tailscaled", "tailscale", "wg-quick", "zerotier",
    "polkit", "getty", "serial-getty", "rescue", "emergency", "goosed", "goose",
)
SEVERING_ACTIONS = {"stop", "kill", "restart", "try-restart", "disable", "mask"}

def is_protected(unit):
    u = (unit or "").lower()
    return any(t.lower() in u for t in PROTECTED_TOKENS)

def is_floor(unit):
    return (unit or "") in CRITICAL_FLOOR

# --------------------------------------------------------------------------- #
# systemd
# --------------------------------------------------------------------------- #
def _sc(scope, privileged):
    base = ["systemctl"]
    if scope == "user":
        return base + ["--user"], None
    pre, e = _priv_prefix("systemctl", privileged)
    return pre, e

def h_services(a):
    scope = a.get("scope", "system")
    op = a.get("op", "list")
    base, gerr = _sc(scope, False)
    if gerr:
        return err(gerr)
    unit = a.get("unit")
    if op == "status" or (op == "list" and unit):
        if not unit:
            return err("op=status needs `unit`")
        rc, out, e = run(base + ["status", "--no-pager", "--lines",
                                 str(int(a.get("lines", 12))), unit])
        return ok(_trunc(out.strip() or e.strip() or f"no status for {unit}"))
    if op == "show":
        if not unit:
            return err("op=show needs `unit`")
        rc, out, e = run(base + ["show", unit] + (["-p", a["properties"]] if a.get("properties") else []))
        return ok(_trunc(out.strip() or e.strip()))
    if op == "cat":
        if not unit:
            return err("op=cat needs `unit`")
        rc, out, e = run(base + ["cat", "--no-pager", unit])
        return ok(_trunc(out.strip() or e.strip()))
    if op == "deps":
        if not unit:
            return err("op=deps needs `unit`")
        flags = ["list-dependencies", "--no-pager", unit]
        if a.get("reverse"):
            flags.insert(1, "--reverse")
        rc, out, e = run(base + flags)
        return ok(_trunc(out.strip() or e.strip()))
    if op == "files":
        rc, out, e = run(base + ["list-unit-files", "--no-pager", "--no-legend",
                                 "--type", a.get("type", "service")])
        rows = [l for l in out.splitlines() if l.strip()]
        pat = a.get("pattern")
        if pat:
            rows = [r for r in rows if pat.lower() in r.lower()]
        return ok(_trunc("\n".join(rows[:int(a.get("limit", 80))]) or "no unit files"))
    # default: list
    cmd = base + ["list-units", "--no-pager", "--no-legend", "--all",
                  "--type", a.get("type", "service")]
    if a.get("state"):
        cmd += ["--state", a["state"]]
    rc, out, e = run(cmd)
    rows = [l for l in out.splitlines() if l.strip()]
    pat = a.get("pattern")
    if pat:
        rows = [r for r in rows if pat.lower() in r.lower()]
    limit = int(a.get("limit", 60))
    extra = f"\n… {len(rows) - limit} more (raise limit or refine pattern)" if len(rows) > limit else ""
    return ok(f"{scope} units ({len(rows)} matched):\n" + ("\n".join(rows[:limit]) or e.strip() or "none") + extra)

_NOUNIT_ACTIONS = {"daemon-reload", "daemon-reexec"}

def h_service(a):
    action = a.get("action")
    scope = a.get("scope", "system")
    force = bool(a.get("force"))
    dry = bool(a.get("dry_run"))
    if not action:
        return err("os_service requires `action`")
    if action in _NOUNIT_ACTIONS:
        units = []
    else:
        u = a.get("unit")
        if not u:
            return err(f"os_service `{action}` requires `unit` (string or list)")
        units = u if isinstance(u, list) else [u]
    # guards (per unit)
    for unit in units:
        if action in SEVERING_ACTIONS and is_floor(unit):
            return err(f"REFUSED (hard floor): `{action} {unit}` would sever the agent's "
                       f"absolute substrate. This is NOT bypassable with force.")
        if action in SEVERING_ACTIONS and is_protected(unit) and not force:
            return err(f"REFUSED: `{action} {unit}` targets a unit the agent depends on "
                       f"(self-preservation guard). Could sever your own bus/session/network/"
                       f"remote access. Pass force=true if certain.")
    base, gerr = _sc(scope, True)
    if gerr:
        return err(gerr)
    cmd = base + [action] + units
    if dry:
        return ok(f"DRY RUN — would execute:\n  {_cmdstr(cmd)}")
    rc, out, e = run(cmd, timeout=60)
    msg = (out + e).strip()
    outcome = f"rc={rc} {msg[:160]}"
    audit("os_service", {"action": action, "units": units, "scope": scope, "force": force}, outcome)
    if rc != 0:
        return err(f"{action} {' '.join(units)} failed (rc {rc}): {msg}{_priv_hint(e)}")
    return ok(f"{action} {' '.join(units) or '(manager)'}: OK{(' — ' + msg) if msg else ''}")

def h_wait(a):
    unit = a.get("unit")
    target = a.get("state", "active")
    if not unit:
        return err("os_wait needs `unit`")
    if target not in ("active", "inactive", "failed"):
        return err("state must be active|inactive|failed")
    scope = a.get("scope", "system")
    base, _ = _sc(scope, False)
    timeout = float(a.get("timeout", 30))
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        rc, out, _ = run(base + ["is-active", unit], timeout=8)
        last = out.strip()
        if target == "active" and last == "active":
            return ok(f"{unit} is active")
        if target == "inactive" and last in ("inactive", "failed", "unknown", "deactivating"):
            return ok(f"{unit} is {last}")
        if target == "failed" and last == "failed":
            return ok(f"{unit} is failed")
        time.sleep(1)
    return err(f"timeout: {unit} did not reach '{target}' within {timeout}s (last: {last or 'unknown'})")

# --------------------------------------------------------------------------- #
# journald
# --------------------------------------------------------------------------- #
def h_journal(a):
    base = ["journalctl"]
    if a.get("scope") == "user":
        base.append("--user")
    # No auto-sudo: journalctl as a normal user reads what the user's groups
    # (adm/systemd-journal/wheel) permit; forcing sudo -n would break that common
    # case. If access is denied, journalctl itself reports it.
    if a.get("boots"):
        rc, out, e = run(base + ["--no-pager", "--list-boots"], timeout=15)
        return ok(_trunc(out.strip() or e.strip() or "no boots"))
    cmd = base + ["--no-pager", "-o", a.get("output", "short"),
                  "-n", str(int(a.get("lines", 50)))]
    if a.get("unit"):
        cmd += ["-u", a["unit"]]
    if a.get("kernel"):
        cmd += ["-k"]
    if a.get("since"):
        cmd += ["--since", a["since"]]
    if a.get("until"):
        cmd += ["--until", a["until"]]
    if a.get("priority") is not None:
        cmd += ["-p", str(a["priority"])]
    if a.get("grep"):
        cmd += ["--grep", a["grep"]]          # server-side PCRE
    if a.get("boot"):
        cmd += ["-b"]
    if a.get("fields"):
        cmd += ["-N"]
    for m in (a.get("match") or []):          # FIELD=value terms
        cmd.append(str(m))
    rc, out, e = run(cmd, timeout=30)
    if rc != 0 and not out:
        return err(f"journalctl failed (rc {rc}): {e.strip()}")
    return ok(_trunc(out.strip() or "(no log lines matched)"))

# --------------------------------------------------------------------------- #
# resources / processes / sensing
# --------------------------------------------------------------------------- #
def h_resources(a):
    out = []
    rc, lo, _ = run(["cat", "/proc/loadavg"], 5)
    if rc == 0:
        out.append("load: " + lo.strip())
    rc, mo, _ = run(["free", "-h"], 5)
    if rc == 0:
        out.append(mo.strip())
    rc, do, _ = run(["df", "-h", "--output=target,size,used,avail,pcent",
                     "-x", "tmpfs", "-x", "devtmpfs"], 8)
    if rc == 0:
        out.append(do.strip())
    if a.get("unit"):
        rc, so, _ = run(["systemctl", "show", a["unit"], "-p",
                         "MemoryCurrent,MemoryPeak,CPUUsageNSec,TasksCurrent,ActiveState"], 8)
        if rc == 0:
            out.append(f"unit {a['unit']}:\n" + so.strip())
    return ok("\n\n".join(out) or "no resource data")

def h_processes(a):
    sort = "-%cpu" if a.get("by", "cpu") == "cpu" else "-%mem"
    n = int(a.get("limit", 15))
    rc, out, e = run(["ps", "-eo", "pid,ppid,user,%cpu,%mem,rss,stat,comm", "--sort", sort], 15)
    if rc != 0:
        return err(f"ps failed: {e.strip()}")
    rows = out.splitlines()
    pat = a.get("pattern")
    head = ([rows[0]] + [r for r in rows[1:] if pat.lower() in r.lower()][:n]) if pat else rows[:n + 1]
    return ok("\n".join(head))

def h_pressure(a):
    out = []
    for r in ("cpu", "memory", "io"):
        try:
            with open(f"/proc/pressure/{r}") as f:
                out.append(f"{r}:\n" + f.read().strip())
        except FileNotFoundError:
            return err("PSI not available (kernel CONFIG_PSI off, or <4.20)")
        except Exception as ex:
            out.append(f"{r}: <error {ex}>")
    return ok("\n".join(out))

def h_net(a):
    if not have("ss"):
        return err("ss not available (install iproute2)")
    if a.get("summary"):
        rc, out, e = run(["ss", "-s"], 10)
        return ok(_trunc(out.strip() or e.strip()))
    flags = "-tulpn" if a.get("listening", True) else "-tanp"
    rc, out, e = run(["ss", flags], 12)
    rows = out.splitlines()
    pat = a.get("pattern")
    if pat:
        rows = [rows[0]] + [r for r in rows[1:] if pat.lower() in r.lower()] if rows else rows
    return ok(_trunc("\n".join(rows) or e.strip() or "no sockets"))

def h_sensors(a):
    out = []
    for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        try:
            t = open(os.path.join(zone, "type")).read().strip()
            mC = int(open(os.path.join(zone, "temp")).read().strip())
            out.append(f"{os.path.basename(zone)} {t}: {mC/1000:.1f}°C")
        except Exception:
            pass
    if have("sensors") and a.get("full"):
        rc, so, _ = run(["sensors"], 10)
        if rc == 0:
            out.append(so.strip())
    return ok("\n".join(out) or "no thermal zones found")

# --------------------------------------------------------------------------- #
# power / sessions / machine settings
# --------------------------------------------------------------------------- #
def h_power(a):
    action = a.get("action", "")
    valid = {"suspend", "hibernate", "hybrid-sleep", "suspend-then-hibernate",
             "reboot", "poweroff", "halt"}
    if action not in valid:
        return err(f"action must be one of: {', '.join(sorted(valid))}")
    tool = "loginctl" if (have("loginctl") and action in {
        "suspend", "hibernate", "hybrid-sleep", "suspend-then-hibernate"}) else "systemctl"
    base = [tool]
    if os.geteuid() != 0 and have("sudo"):
        base = ["sudo", "-n", tool]
    cmd = base + [action]
    if bool(a.get("dry_run")):
        return ok(f"DRY RUN — would execute:\n  {_cmdstr(cmd)}")
    if not bool(a.get("confirm")):
        # capability probe for context
        cap = ""
        if have("systemctl"):
            verb = {"poweroff": "can-poweroff", "reboot": "can-reboot", "suspend": "can-suspend",
                    "hibernate": "can-hibernate", "halt": "can-halt"}.get(action)
            if verb:
                _, co, _ = run(["systemctl", verb], 6)
                cap = f" (systemctl {verb}: {co.strip() or 'unknown'})"
        return err(f"REFUSED: `{action}` is irreversible/disruptive. Re-call with confirm=true.{cap}")
    rc, out, e = run(cmd, 20)
    audit("os_power", {"action": action}, f"rc={rc} {(out + e).strip()[:120]}")
    if rc != 0:
        return err(f"{action} failed (rc {rc}): {(out + e).strip()}{_priv_hint(e)}")
    return ok(f"{action}: requested")

def h_session(a):
    if not have("loginctl"):
        return err("loginctl not available")
    op = a.get("op", "list-sessions")
    if op == "list-sessions":
        rc, out, e = run(["loginctl", "list-sessions", "--no-pager"], 10)
    elif op == "list-users":
        rc, out, e = run(["loginctl", "list-users", "--no-pager"], 10)
    elif op == "session-status":
        if not a.get("id"):
            return err("op=session-status needs `id`")
        rc, out, e = run(["loginctl", "session-status", "--no-pager", str(a["id"])], 10)
    elif op == "inhibitors":
        if have("systemd-inhibit"):
            rc, out, e = run(["systemd-inhibit", "--list", "--no-pager"], 10)
        else:
            return err("systemd-inhibit not available")
    else:
        return err("op must be list-sessions|list-users|session-status|inhibitors")
    return ok(_trunc(out.strip() or e.strip() or "(none)"))

def _machine_tool(a, binary, status_args, set_map, label):
    """Shared pattern for timedatectl/hostnamectl/localectl: op=status|set-*."""
    if not have(binary):
        return err(f"{binary} not available")
    op = a.get("op", "status")
    if op == "status":
        rc, out, e = run([binary] + status_args, 10)
        return ok(_trunc(out.strip() or e.strip()))
    if op in set_map:
        val = a.get("value")
        need_val, argv = set_map[op]
        if need_val and val is None:
            return err(f"op={op} needs `value`")
        base, gerr = _priv_prefix(binary, True)
        if gerr:
            return err(gerr)
        cmd = base + argv + ([str(val)] if need_val else [])
        if bool(a.get("dry_run")):
            return ok(f"DRY RUN — would execute:\n  {_cmdstr(cmd)}")
        if not bool(a.get("force")):
            return err(f"REFUSED: `{op}` changes machine {label}. Pass force=true (or dry_run=true).")
        rc, out, e = run(cmd, 15)
        audit(f"os_{label}", {"op": op, "value": val}, f"rc={rc} {(out + e).strip()[:120]}")
        if rc != 0:
            return err(f"{op} failed (rc {rc}): {(out + e).strip()}{_priv_hint(e)}")
        return ok(f"{op} {val if need_val else ''}: OK")
    return err(f"op must be status|{'|'.join(set_map)}")

def h_time(a):
    return _machine_tool(a, "timedatectl", ["status"], {
        "set-timezone": (True, ["set-timezone"]),
        "set-ntp": (True, ["set-ntp"]),
        "list-timezones": (False, ["list-timezones", "--no-pager"]),
    }, "time")

def h_hostname(a):
    return _machine_tool(a, "hostnamectl", ["status"], {
        "set-hostname": (True, ["set-hostname"]),
    }, "hostname")

def h_locale(a):
    return _machine_tool(a, "localectl", ["status"], {
        "set-locale": (True, ["set-locale"]),
        "set-keymap": (True, ["set-keymap"]),
        "list-locales": (False, ["list-locales", "--no-pager"]),
    }, "locale")

# --------------------------------------------------------------------------- #
# D-Bus
# --------------------------------------------------------------------------- #
def h_dbus(a):
    if not have("busctl"):
        return err("busctl not available (install systemd's busctl)")
    op = a.get("op", "list")
    busflag = "--user" if a.get("scope") == "user" else "--system"
    tmo = int(a.get("timeout", 20))
    def need(*keys):
        miss = [k for k in keys if not a.get(k)]
        return f"op={op} needs {', '.join(keys)} (missing {', '.join(miss)})" if miss else None
    if op == "list":
        rc, out, e = run(["busctl", busflag, "--no-pager", "list"], tmo)
    elif op == "tree":
        if (m := need("service")):
            return err(m)
        rc, out, e = run(["busctl", busflag, "--no-pager", "tree", a["service"]], tmo)
    elif op == "introspect":
        if (m := need("service", "path")):
            return err(m)
        rc, out, e = run(["busctl", busflag, "--no-pager", "introspect", a["service"], a["path"]], tmo)
    elif op == "get-property":
        if (m := need("service", "path", "interface", "member")):
            return err(m)
        rc, out, e = run(["busctl", busflag, "--no-pager", "get-property",
                          a["service"], a["path"], a["interface"], a["member"]], tmo)
    elif op in ("call", "set-property"):
        keys = ("service", "path", "interface", "member")
        if (m := need(*keys)):
            return err(m)
        cmd = ["busctl", busflag, "--no-pager", op, a["service"], a["path"],
               a["interface"], a["member"]]
        if a.get("signature"):
            cmd.append(a["signature"])
            cmd += [str(x) for x in (a.get("args") or [])]
        if bool(a.get("dry_run")):
            return ok(f"DRY RUN — would execute:\n  {_cmdstr(cmd)}")
        if not bool(a.get("force")):
            return err(f"REFUSED: op={op} has side effects. Pass force=true (or dry_run=true).")
        rc, out, e = run(cmd, tmo)
        audit("os_dbus", {"op": op, "service": a["service"], "member": a["member"]},
              f"rc={rc} {(out + e).strip()[:120]}")
    else:
        return err("op must be list|tree|introspect|get-property|set-property|call")
    if rc != 0 and not out:
        return err(f"busctl {op} failed (rc {rc}): {e.strip()}")
    return ok(_trunc(out.strip() or "(empty)"))

# --------------------------------------------------------------------------- #
# notify / diag / reload
# --------------------------------------------------------------------------- #
def h_notify(a):
    summary = a.get("summary") or "goose"
    body = a.get("body", "")
    urgency = a.get("urgency", "normal")
    if have("notify-send"):
        rc, _, e = run(["notify-send", "-u", urgency, "-a", "os-control-mcp", summary, body], 10)
        return ok(f"notification sent: {summary}") if rc == 0 else err(f"notify-send failed: {e.strip()}")
    if have("gdbus"):
        rc, _, e = run(["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                        "--object-path", "/org/freedesktop/Notifications",
                        "--method", "org.freedesktop.Notifications.Notify",
                        "os-control-mcp", "0", "", summary, body, "[]", "{}", "5000"], 10)
        return ok(f"notification sent: {summary}") if rc == 0 else err(f"gdbus notify failed: {e.strip()}")
    return err("no notification backend (install libnotify's notify-send, or gdbus)")

def h_diag(a):
    euid = os.geteuid()
    bins = {b: have(b) for b in ("systemctl", "loginctl", "journalctl", "busctl",
                                 "timedatectl", "hostnamectl", "localectl", "ss",
                                 "notify-send", "gdbus", "sudo")}
    lines = [
        f"os-control-mcp {__version__}",
        f"euid: {euid} ({'root' if euid == 0 else 'unprivileged — system mutations use sudo -n / polkit'})",
        "binaries: " + ", ".join(f"{b}{'✓' if v else '✗'}" for b, v in bins.items()),
    ]
    rc, out, _ = run(["systemctl", "is-system-running"], 8)
    lines.append(f"system manager: {out.strip() or 'unknown'} (rc {rc})")
    rc, out, _ = run(["systemctl", "--user", "is-system-running"], 8)
    lines.append(f"user manager: {out.strip() or 'n/a'}")
    if have("busctl"):
        rc, out, _ = run(["busctl", "--system", "--no-pager", "list"], 8)
        lines.append(f"system bus: {'reachable' if rc == 0 else 'unreachable'} ({len(out.splitlines())} names)")
    lines.append(f"safety: hard floor {len(CRITICAL_FLOOR)} units (never severable) + "
                 f"{len(PROTECTED_TOKENS)} protected tokens (force to override); audit -> {_state_dir()}/audit.jsonl")
    return ok("\n".join(lines))

def h_reload(a):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}) + "\n")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable] + sys.argv)

# --------------------------------------------------------------------------- #
# catalog
# --------------------------------------------------------------------------- #
_SCOPE = {"type": "string", "enum": ["system", "user"], "description": "systemd scope (default system)"}
_RO = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
_MUT = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}

TOOLS = [
    {"name": "os_diag", "title": "Diagnostics", "annotations": _RO,
     "description": "Health dump — run FIRST when anything misbehaves. euid (root vs not), which backend binaries exist (systemctl/loginctl/journalctl/busctl/timedatectl/hostnamectl/localectl/ss/notify-send/gdbus/sudo), system+user manager state, system-bus reachability, and the safety-layer status (hard floor + protected tokens + audit-log path).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "os_services", "title": "Inspect services", "annotations": _RO,
     "description": "Inspect systemd units (read-only). op=list (default; filter type/state/pattern) | status (full `systemctl status` of `unit`) | show (parseable key=value props; `properties` to select) | cat (merged unit + drop-ins) | deps (list-dependencies, `reverse` for reverse) | files (list-unit-files). `scope`=system|user.",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string", "enum": ["list", "status", "show", "cat", "deps", "files"]}, "unit": {"type": "string"}, "type": {"type": "string"}, "state": {"type": "string"}, "pattern": {"type": "string"}, "properties": {"type": "string"}, "reverse": {"type": "boolean"}, "limit": {"type": "number"}, "lines": {"type": "number"}, "scope": _SCOPE}}},
    {"name": "os_service", "title": "Control services", "annotations": {**_MUT, "idempotentHint": False},
     "description": "Run a systemd action: start|stop|restart|reload|try-restart|enable|disable|mask|unmask|kill|reset-failed|revert|daemon-reload|daemon-reexec. `unit` may be a string OR a list (batch); omit it for daemon-reload/daemon-reexec. dry_run=true returns the exact command without running. SAFETY: hard floor refuses severing dbus/logind/init even with force; self-preservation guard refuses severing units the agent depends on unless force=true. System mutations use sudo -n when not root. Every mutation is audit-logged.",
     "inputSchema": {"type": "object", "properties": {"unit": {"type": ["string", "array"], "items": {"type": "string"}}, "action": {"type": "string"}, "scope": _SCOPE, "force": {"type": "boolean"}, "dry_run": {"type": "boolean"}}, "required": ["action"]}},
    {"name": "os_wait", "title": "Wait for unit state", "annotations": {**_RO, "readOnlyHint": False},
     "description": "Block until `unit` reaches `state` (active|inactive|failed) or `timeout` seconds (default 30). Poll-based; use after a start/stop instead of guessing a sleep.",
     "inputSchema": {"type": "object", "properties": {"unit": {"type": "string"}, "state": {"type": "string", "enum": ["active", "inactive", "failed"]}, "timeout": {"type": "number"}, "scope": _SCOPE}, "required": ["unit"]}},
    {"name": "os_journal", "title": "Query the journal", "annotations": _RO,
     "description": "Read journald (journalctl). unit, lines (50), since/until, priority (0-7), grep (server-side PCRE), kernel=true (-k dmesg), boot=true (current boot), boots=true (--list-boots), fields=true (-N field names), match=['_SYSTEMD_UNIT=x','_PID=…'] arbitrary field filters, output (short/json/json-pretty/cat). scope=system|user.",
     "inputSchema": {"type": "object", "properties": {"unit": {"type": "string"}, "lines": {"type": "number"}, "since": {"type": "string"}, "until": {"type": "string"}, "priority": {"type": ["string", "number"]}, "grep": {"type": "string"}, "kernel": {"type": "boolean"}, "boot": {"type": "boolean"}, "boots": {"type": "boolean"}, "fields": {"type": "boolean"}, "match": {"type": "array", "items": {"type": "string"}}, "output": {"type": "string"}, "scope": _SCOPE}}},
    {"name": "os_resources", "title": "Resource snapshot", "annotations": _RO,
     "description": "Host pressure: load avg, `free -h`, `df -h` (real fs). Pass `unit` for its MemoryCurrent/MemoryPeak/CPUUsageNSec/TasksCurrent/ActiveState.",
     "inputSchema": {"type": "object", "properties": {"unit": {"type": "string"}}}},
    {"name": "os_processes", "title": "Top processes", "annotations": _RO,
     "description": "Top processes by `by`=cpu|mem (default cpu), `limit` (15), optional `pattern`. Shows pid/ppid/user/%cpu/%mem/rss/stat/comm.",
     "inputSchema": {"type": "object", "properties": {"by": {"type": "string", "enum": ["cpu", "mem"]}, "limit": {"type": "number"}, "pattern": {"type": "string"}}}},
    {"name": "os_pressure", "title": "PSI pressure", "annotations": _RO,
     "description": "Pressure Stall Information from /proc/pressure/{cpu,memory,io} — the real 'is the box starving' signal (some/full avg10/avg60/avg300). Read-only.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "os_net", "title": "Sockets / network", "annotations": _RO,
     "description": "Socket stats via ss. Default: listening TCP/UDP + owning process (ss -tulpn). summary=true → ss -s (counts by state). listening=false → all connections. Optional `pattern`. Read-only.",
     "inputSchema": {"type": "object", "properties": {"summary": {"type": "boolean"}, "listening": {"type": "boolean"}, "pattern": {"type": "string"}}}},
    {"name": "os_sensors", "title": "Thermal sensors", "annotations": _RO,
     "description": "Temperatures from /sys/class/thermal/thermal_zone*. full=true also runs `sensors` (lm_sensors) if present. Read-only.",
     "inputSchema": {"type": "object", "properties": {"full": {"type": "boolean"}}}},
    {"name": "os_power", "title": "Power control", "annotations": _MUT,
     "description": "Power state via logind/systemd: suspend|hibernate|hybrid-sleep|suspend-then-hibernate|reboot|poweroff|halt. IRREVERSIBLE — refused unless confirm=true (the refusal includes the systemctl can-* capability probe). dry_run=true previews. sudo -n when not root. Audit-logged.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}, "confirm": {"type": "boolean"}, "dry_run": {"type": "boolean"}}, "required": ["action"]}},
    {"name": "os_session", "title": "Login sessions", "annotations": _RO,
     "description": "logind sessions/users/inhibitors (read-only). op=list-sessions (default) | list-users | session-status (`id`) | inhibitors (active power/idle/sleep locks via systemd-inhibit --list).",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string", "enum": ["list-sessions", "list-users", "session-status", "inhibitors"]}, "id": {"type": ["string", "number"]}}}},
    {"name": "os_time", "title": "Time / timezone / NTP", "annotations": {**_MUT, "destructiveHint": False},
     "description": "timedatectl. op=status (default) | list-timezones | set-timezone (value=Area/City) | set-ntp (value=true|false). Writes need force=true (or dry_run=true) + root/polkit. Audit-logged.",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string"}, "value": {"type": ["string", "boolean"]}, "force": {"type": "boolean"}, "dry_run": {"type": "boolean"}}}},
    {"name": "os_hostname", "title": "Hostname", "annotations": {**_MUT, "destructiveHint": False},
     "description": "hostnamectl. op=status (default) | set-hostname (value=NAME). Writes need force=true (or dry_run=true) + root/polkit. Audit-logged.",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string"}, "value": {"type": "string"}, "force": {"type": "boolean"}, "dry_run": {"type": "boolean"}}}},
    {"name": "os_locale", "title": "Locale / keymap", "annotations": {**_MUT, "destructiveHint": False},
     "description": "localectl. op=status (default) | list-locales | set-locale (value=LANG=…) | set-keymap (value=KEYMAP). Writes need force=true (or dry_run=true) + root/polkit. Audit-logged.",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string"}, "value": {"type": "string"}, "force": {"type": "boolean"}, "dry_run": {"type": "boolean"}}}},
    {"name": "os_notify", "title": "Desktop notification", "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
     "description": "Send a desktop notification to the logged-in user via the session bus (notify-send, else gdbus). summary (title), body, urgency=low|normal|critical.",
     "inputSchema": {"type": "object", "properties": {"summary": {"type": "string"}, "body": {"type": "string"}, "urgency": {"type": "string", "enum": ["low", "normal", "critical"]}}, "required": ["summary"]}},
    {"name": "os_dbus", "title": "D-Bus", "annotations": _MUT,
     "description": "D-Bus via busctl. op=list | tree (`service`) | introspect (`service`,`path`) | get-property (`service`,`path`,`interface`,`member`) | set-property | call (+ optional `signature`+`args`). scope=system|user. `timeout` secs. set-property/call have side effects → force=true (or dry_run=true). Reads are free. Audit-logged on writes.",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string"}, "service": {"type": "string"}, "path": {"type": "string"}, "interface": {"type": "string"}, "member": {"type": "string"}, "signature": {"type": "string"}, "args": {"type": "array"}, "scope": _SCOPE, "timeout": {"type": "number"}, "force": {"type": "boolean"}, "dry_run": {"type": "boolean"}}}},
    {"name": "os_reload", "title": "Reload server", "annotations": _MUT,
     "description": "Hot-reload this server's own code in place (execv) so edits + new tools take effect with no /mcp reconnect.",
     "inputSchema": {"type": "object", "properties": {}}},
]

HANDLERS = {
    "os_diag": h_diag, "os_services": h_services, "os_service": h_service, "os_wait": h_wait,
    "os_journal": h_journal, "os_resources": h_resources, "os_processes": h_processes,
    "os_pressure": h_pressure, "os_net": h_net, "os_sensors": h_sensors,
    "os_power": h_power, "os_session": h_session, "os_time": h_time, "os_hostname": h_hostname,
    "os_locale": h_locale, "os_notify": h_notify, "os_dbus": h_dbus, "os_reload": h_reload,
}

INSTRUCTIONS = (
    "Drive the Linux host through its sanctioned interfaces — systemd, logind, journald, "
    "D-Bus, machine settings — not raw PID hacks. Loop: os_diag (health) → "
    "os_services/os_journal/os_resources/os_processes/os_pressure/os_net/os_sensors/os_session "
    "(observe) → os_service/os_power/os_dbus/os_time/os_hostname/os_locale/os_notify (act) → "
    "os_wait / re-read (confirm). Safety: a hard floor refuses severing dbus/logind/init even "
    "with force; the self-preservation guard refuses severing units the agent depends on unless "
    "force=true; os_power needs confirm=true; machine-setting writes + os_dbus call need force=true; "
    "any mutation accepts dry_run=true to preview. System mutations need root/polkit (sudo -n when "
    "not root). Every mutation is audit-logged. Works with any MCP client. Pairs with screen-mcp "
    "(GUI), NATS, and A2A for a full-stack agent."
)

MUTATING = {"os_service", "os_power", "os_dbus", "os_time", "os_hostname", "os_locale", "os_notify", "os_reload"}

# --------------------------------------------------------------------------- #
# stdio JSON-RPC loop
# --------------------------------------------------------------------------- #
def reply(mid, result=None, error=None):
    m = {"jsonrpc": "2.0", "id": mid}
    if error:
        m["error"] = error
    else:
        m["result"] = result
    sys.stdout.write(json.dumps(m) + "\n")
    sys.stdout.flush()

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            reply(mid, {"protocolVersion": "2025-11-25",
                        "capabilities": {"tools": {"listChanged": True}},
                        "serverInfo": {"name": "os-control-mcp", "version": __version__},
                        "instructions": INSTRUCTIONS})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            reply(mid, {"tools": TOOLS})
        elif method == "tools/call":
            name = msg.get("params", {}).get("name")
            args = msg.get("params", {}).get("arguments", {}) or {}
            handler = HANDLERS.get(name)
            if not handler:
                reply(mid, error={"code": -32601, "message": f"unknown tool: {name}"})
                continue
            try:
                reply(mid, handler(args))
            except Exception as e:
                import traceback
                log(traceback.format_exc())
                reply(mid, err(f"{name} crashed: {e} (see {_state_dir()}/err.log)"))
        elif mid is not None:
            reply(mid, error={"code": -32601, "message": f"unknown method: {method}"})

if __name__ == "__main__":
    main()
