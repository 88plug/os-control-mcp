#!/usr/bin/env python3
"""os-control-mcp — the sanctioned OS "motor cortex" for an agent on a Linux box.

A thin MCP stdio server that drives the host through its STRUCTURED, sanctioned
interfaces — systemd (services/timers/resources), logind (power), journald (logs),
and the D-Bus system/session buses — instead of raw kill/PID hacks. It is the
service-and-system counterpart to screen-mcp's GUI motor cortex.

Design:
  * Pure standard library. No pip deps. Every capability shells out to the tools
    that already define the sanctioned interface: systemctl, loginctl, journalctl,
    busctl, notify-send/gdbus. If a tool is absent, the relevant op degrades with a
    clear message (os_diag tells you what's available).
  * Read-first. Listing/status/journal/resources are read-only. Mutations
    (service actions, power, dbus call) are clearly annotated destructive.
  * Self-preservation guard (the analog of screen-mcp's user-takeover guard): a
    destructive systemd action against a unit the AGENT ITSELF STANDS ON — dbus,
    logind, sshd, NetworkManager, tailscaled, the user session, goosed, … — is
    REFUSED unless force=true. Don't saw off the branch you're sitting on.
  * scope=system|user everywhere systemd is involved. System mutations need root
    or polkit; when euid != 0 we try `sudo -n` and, failing that, say so plainly.

OPS NOTES (read before changing):
  * os_diag first when anything misbehaves — it reports euid, bus reachability,
    and which backend binaries exist + are usable.
  * On any tool exception the dispatcher writes the full traceback to
    $XDG_STATE_HOME/os-control-mcp/err.log (the JSON-RPC error only carries the
    message). Read it to debug.
  * os_reload hot-reloads this file in place (execv) so edits + new tools land
    with no /mcp reconnect.
"""
import sys, os, json, time, shutil, subprocess, shlex

__version__ = "0.1.0"

# --------------------------------------------------------------------------- #
# small helpers
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

def _txt(s):
    return {"type": "text", "text": s}

def ok(s):
    return {"content": [_txt(s)]}

def err(s):
    return {"content": [_txt(s)], "isError": True}

def have(binname):
    return shutil.which(binname) is not None

def run(cmd, timeout=25, input_text=None):
    """Run argv list; return (rc, stdout, stderr). Never raises for non-zero."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           input=input_text)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s: {' '.join(shlex.quote(c) for c in cmd)}"

# --------------------------------------------------------------------------- #
# self-preservation guard
# --------------------------------------------------------------------------- #
# Tokens that, if found in a unit name, mean a destructive action could sever the
# agent's own footing (its bus, login session, network/remote access, or itself).
PROTECTED_TOKENS = (
    "dbus", "dbus-broker",
    "systemd-logind", "systemd-journald", "systemd-udevd",
    "systemd-resolved", "systemd-networkd", "NetworkManager", "wpa_supplicant",
    "iwd", "systemd-user-sessions", "user@", "user-runtime-dir",
    "ssh", "sshd", "tailscaled", "tailscale", "wg-quick", "zerotier",
    "polkit", "getty", "serial-getty", "rescue", "emergency",
    "goosed", "goose",
)
# systemd verbs that are "severing" (can take a unit down / lock it out).
SEVERING_ACTIONS = {"stop", "kill", "restart", "try-restart", "disable", "mask"}

def is_protected(unit):
    u = (unit or "").lower()
    return any(tok.lower() in u for tok in PROTECTED_TOKENS)

# --------------------------------------------------------------------------- #
# systemd plumbing
# --------------------------------------------------------------------------- #
def _systemctl_base(scope, privileged):
    """Build the systemctl prefix for the scope, adding sudo -n for privileged
    system ops when we're not already root."""
    base = ["systemctl"]
    if scope == "user":
        base.append("--user")
        return base, None
    # system scope
    if privileged and os.geteuid() != 0:
        if have("sudo"):
            return ["sudo", "-n", "systemctl"], None
        return base, "system action needs root and `sudo` is not installed"
    return base, None

def _journal_base(scope):
    base = ["journalctl"]
    if scope == "user":
        base.append("--user")
    elif os.geteuid() != 0 and have("sudo"):
        base = ["sudo", "-n", "journalctl"]
    return base

# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #
def h_diag(a):
    euid = os.geteuid()
    bins = {b: have(b) for b in ("systemctl", "loginctl", "journalctl", "busctl",
                                 "notify-send", "gdbus", "sudo")}
    lines = [
        f"os-control-mcp {__version__}",
        f"euid: {euid} ({'root' if euid == 0 else 'unprivileged — system mutations use sudo -n / polkit'})",
        f"binaries: " + ", ".join(f"{b}{'✓' if ok_ else '✗'}" for b, ok_ in bins.items()),
    ]
    # systemd alive?
    rc, out, _ = run(["systemctl", "is-system-running"], timeout=8)
    lines.append(f"system manager: {out.strip() or 'unknown'} (rc {rc})")
    rc, out, _ = run(["systemctl", "--user", "is-system-running"], timeout=8)
    lines.append(f"user manager: {out.strip() or 'n/a'}")
    # buses
    for label, env in (("session bus", "DBUS_SESSION_BUS_ADDRESS"),
                       ("system bus", None)):
        if label == "session bus":
            lines.append(f"{label}: {'set' if os.environ.get(env) else 'unset'}")
    if have("busctl"):
        rc, out, _ = run(["busctl", "--system", "--no-pager", "list"], timeout=8)
        lines.append(f"system bus: {'reachable' if rc == 0 else 'unreachable'} "
                     f"({len(out.splitlines())} names)")
    lines.append(f"self-preservation guard: {len(PROTECTED_TOKENS)} protected tokens "
                 f"(stop/restart/disable/mask/kill blocked on matches unless force=true)")
    return ok("\n".join(lines))

def h_services(a):
    scope = a.get("scope", "system")
    unit = a.get("unit")
    base, gerr = _systemctl_base(scope, privileged=False)
    if gerr:
        return err(gerr)
    if unit:
        rc, out, e = run(base + ["status", "--no-pager", "--lines",
                                 str(int(a.get("lines", 12))), unit])
        return ok(out.strip() or e.strip() or f"no status for {unit}")
    cmd = base + ["list-units", "--no-pager", "--no-legend", "--all",
                  "--type", a.get("type", "service")]
    state = a.get("state")
    if state:
        cmd += ["--state", state]
    rc, out, e = run(cmd)
    rows = [ln for ln in out.splitlines() if ln.strip()]
    pat = a.get("pattern")
    if pat:
        rows = [r for r in rows if pat.lower() in r.lower()]
    limit = int(a.get("limit", 60))
    head = rows[:limit]
    body = "\n".join(head) if head else (e.strip() or "no matching units")
    extra = f"\n… {len(rows) - limit} more (raise limit or refine pattern)" if len(rows) > limit else ""
    return ok(f"{scope} units ({len(rows)} shown of matches):\n{body}{extra}")

def h_service(a):
    unit = a.get("unit")
    action = a.get("action")
    scope = a.get("scope", "system")
    force = bool(a.get("force"))
    if not unit or not action:
        return err("os_service requires `unit` and `action`")
    if action in SEVERING_ACTIONS and is_protected(unit) and not force:
        return err(
            f"REFUSED: `{action} {unit}` targets a unit the agent depends on "
            f"(matched the self-preservation guard). This could sever your own "
            f"bus / session / network / remote access. Pass force=true if you are "
            f"certain. Protected tokens: {', '.join(PROTECTED_TOKENS)}")
    base, gerr = _systemctl_base(scope, privileged=True)
    if gerr:
        return err(gerr)
    rc, out, e = run(base + [action, unit], timeout=40)
    msg = (out + e).strip()
    if rc != 0:
        hint = ""
        if "password is required" in e or "sudo:" in e:
            hint = "\n(needs root: run goosed/this server as root, configure passwordless sudo for systemctl, or use a polkit rule)"
        elif "Interactive authentication required" in e:
            hint = "\n(polkit wants interactive auth — not available headless; run as root or add a polkit rule)"
        return err(f"{action} {unit} failed (rc {rc}): {msg}{hint}")
    return ok(f"{action} {unit}: OK{(' — ' + msg) if msg else ''}")

def h_journal(a):
    base = _journal_base(a.get("scope", "system"))
    cmd = base + ["--no-pager", "-o", a.get("output", "short"),
                  "-n", str(int(a.get("lines", 50)))]
    if a.get("unit"):
        cmd += ["-u", a["unit"]]
    if a.get("since"):
        cmd += ["--since", a["since"]]
    if a.get("priority"):
        cmd += ["-p", str(a["priority"])]
    if a.get("boot"):
        cmd += ["-b"]
    rc, out, e = run(cmd, timeout=30)
    if rc != 0 and not out:
        return err(f"journalctl failed (rc {rc}): {e.strip()}")
    grep = a.get("grep")
    if grep:
        out = "\n".join(l for l in out.splitlines() if grep.lower() in l.lower())
    return ok(out.strip() or "(no log lines matched)")

def h_power(a):
    action = a.get("action", "")
    valid = {"suspend", "hibernate", "hybrid-sleep", "suspend-then-hibernate",
             "reboot", "poweroff", "halt"}
    if action not in valid:
        return err(f"os_power action must be one of: {', '.join(sorted(valid))}")
    if not bool(a.get("confirm")):
        return err(f"REFUSED: `{action}` is irreversible/disruptive. Re-call with "
                   f"confirm=true to actually {action} the machine.")
    tool = "loginctl" if have("loginctl") and action in {
        "suspend", "hibernate", "hybrid-sleep", "suspend-then-hibernate"} else "systemctl"
    base = [tool]
    if os.geteuid() != 0 and have("sudo"):
        base = ["sudo", "-n", tool]
    rc, out, e = run(base + [action], timeout=20)
    if rc != 0:
        return err(f"{action} failed (rc {rc}): {(out + e).strip()}")
    return ok(f"{action}: requested")

def h_processes(a):
    by = a.get("by", "cpu")
    sort = "-%cpu" if by == "cpu" else "-%mem"
    n = int(a.get("limit", 15))
    rc, out, e = run(["ps", "-eo", "pid,ppid,user,%cpu,%mem,rss,comm",
                      "--sort", sort], timeout=15)
    if rc != 0:
        return err(f"ps failed: {e.strip()}")
    rows = out.splitlines()
    pat = a.get("pattern")
    if pat:
        head = [rows[0]] + [r for r in rows[1:] if pat.lower() in r.lower()][:n]
    else:
        head = rows[:n + 1]
    return ok("\n".join(head))

def h_resources(a):
    out = []
    rc, lo, _ = run(["cat", "/proc/loadavg"], timeout=5)
    if rc == 0:
        out.append("load: " + lo.strip())
    rc, mo, _ = run(["free", "-h"], timeout=5)
    if rc == 0:
        out.append(mo.strip())
    rc, do, _ = run(["df", "-h", "--output=target,size,used,avail,pcent",
                     "-x", "tmpfs", "-x", "devtmpfs"], timeout=8)
    if rc == 0:
        out.append(do.strip())
    if a.get("unit"):
        rc, so, _ = run(["systemctl", "show", a["unit"], "-p",
                         "MemoryCurrent,CPUUsageNSec,TasksCurrent,ActiveState"], timeout=8)
        if rc == 0:
            out.append(f"unit {a['unit']}:\n" + so.strip())
    return ok("\n\n".join(out) or "no resource data")

def h_notify(a):
    summary = a.get("summary") or a.get("message") or "goose"
    body = a.get("body", "") if a.get("summary") else ""
    urgency = a.get("urgency", "normal")
    if have("notify-send"):
        rc, _, e = run(["notify-send", "-u", urgency, "-a", "os-control-mcp",
                        summary, body], timeout=10)
        if rc == 0:
            return ok(f"notification sent: {summary}")
        return err(f"notify-send failed: {e.strip()}")
    if have("gdbus"):
        rc, _, e = run(["gdbus", "call", "--session",
                        "--dest", "org.freedesktop.Notifications",
                        "--object-path", "/org/freedesktop/Notifications",
                        "--method", "org.freedesktop.Notifications.Notify",
                        "os-control-mcp", "0", "", summary, body, "[]", "{}", "5000"],
                       timeout=10)
        if rc == 0:
            return ok(f"notification sent: {summary}")
        return err(f"gdbus notify failed: {e.strip()}")
    return err("no notification backend (install libnotify's notify-send, or gdbus)")

def h_dbus(a):
    if not have("busctl"):
        return err("busctl not available (install systemd's busctl)")
    op = a.get("op", "list")
    busflag = "--user" if a.get("scope") == "user" else "--system"
    if op == "list":
        rc, out, e = run(["busctl", busflag, "--no-pager", "list"], timeout=12)
    elif op == "tree":
        if not a.get("service"):
            return err("op=tree needs `service`")
        rc, out, e = run(["busctl", busflag, "--no-pager", "tree", a["service"]], timeout=15)
    elif op == "introspect":
        if not (a.get("service") and a.get("path")):
            return err("op=introspect needs `service` and `path`")
        rc, out, e = run(["busctl", busflag, "--no-pager", "introspect",
                          a["service"], a["path"]], timeout=15)
    elif op == "call":
        for k in ("service", "path", "interface", "member"):
            if not a.get(k):
                return err(f"op=call needs service, path, interface, member (missing {k})")
        if not bool(a.get("force")):
            return err("REFUSED: op=call invokes a method (side effects possible). "
                       "Pass force=true to proceed.")
        cmd = ["busctl", busflag, "--no-pager", "call", a["service"], a["path"],
               a["interface"], a["member"]]
        if a.get("signature"):
            cmd.append(a["signature"])
            cmd += [str(x) for x in (a.get("args") or [])]
        rc, out, e = run(cmd, timeout=20)
    else:
        return err("op must be list|tree|introspect|call")
    if rc != 0 and not out:
        return err(f"busctl {op} failed (rc {rc}): {e.strip()}")
    body = out.strip()
    if len(body) > 8000:
        body = body[:8000] + "\n… (truncated)"
    return ok(body or "(empty)")

def h_reload(a):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0",
                                 "method": "notifications/tools/list_changed"}) + "\n")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable] + sys.argv)

# --------------------------------------------------------------------------- #
# tool catalog
# --------------------------------------------------------------------------- #
_SCOPE = {"type": "string", "enum": ["system", "user"],
          "description": "systemd scope (default system)"}

TOOLS = [
    {"name": "os_diag", "title": "Diagnostics",
     "annotations": {"readOnlyHint": True, "destructiveHint": False},
     "description": "Health dump — check this FIRST when anything misbehaves. Reports euid (root vs unprivileged), which backend binaries exist (systemctl/loginctl/journalctl/busctl/notify-send/gdbus/sudo), whether the system + user systemd managers are running, system-bus reachability, and the self-preservation guard status.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "os_services", "title": "List / status services",
     "annotations": {"readOnlyHint": True, "destructiveHint": False},
     "description": "List systemd units, or get the status of one. With `unit`: full `systemctl status` (control `lines`). Without: list units filtered by `type` (service/timer/socket/target/mount, default service), `state` (running/failed/active/…), and a substring `pattern`. `scope`=system|user. Read-only.",
     "inputSchema": {"type": "object", "properties": {"unit": {"type": "string"}, "type": {"type": "string"}, "state": {"type": "string"}, "pattern": {"type": "string", "description": "case-insensitive substring filter"}, "limit": {"type": "number"}, "lines": {"type": "number"}, "scope": _SCOPE}}},
    {"name": "os_service", "title": "Control a service",
     "annotations": {"readOnlyHint": False, "destructiveHint": True},
     "description": "Run a systemd action on a unit: start|stop|restart|reload|try-restart|enable|disable|mask|unmask|kill. `scope`=system|user. SELF-PRESERVATION GUARD: a severing action (stop/restart/disable/mask/kill) on a unit the agent depends on (dbus, logind, sshd, NetworkManager, tailscaled, the user session, goosed, …) is REFUSED unless force=true — so you can't sever your own footing by accident. System mutations need root/polkit (uses sudo -n when not root).",
     "inputSchema": {"type": "object", "properties": {"unit": {"type": "string"}, "action": {"type": "string", "enum": ["start", "stop", "restart", "reload", "try-restart", "enable", "disable", "mask", "unmask", "kill"]}, "scope": _SCOPE, "force": {"type": "boolean", "description": "bypass the self-preservation guard"}}, "required": ["unit", "action"]}},
    {"name": "os_journal", "title": "Query the journal",
     "annotations": {"readOnlyHint": True, "destructiveHint": False},
     "description": "Read journald logs (journalctl). Filter by `unit`, `lines` (default 50), `since` (e.g. '10 min ago', '2026-06-24'), `priority` (0-7 or emerg..debug), `boot` (current boot only), and a post-filter `grep` substring. `scope`=system|user. Read-only.",
     "inputSchema": {"type": "object", "properties": {"unit": {"type": "string"}, "lines": {"type": "number"}, "since": {"type": "string"}, "priority": {"type": ["string", "number"]}, "boot": {"type": "boolean"}, "grep": {"type": "string"}, "output": {"type": "string"}, "scope": _SCOPE}}},
    {"name": "os_resources", "title": "Resource snapshot",
     "annotations": {"readOnlyHint": True, "destructiveHint": False},
     "description": "Snapshot of host pressure: load average, `free -h` memory, and `df -h` disk (real filesystems). Pass `unit` to also show that unit's MemoryCurrent/CPUUsageNSec/TasksCurrent/ActiveState. Read-only.",
     "inputSchema": {"type": "object", "properties": {"unit": {"type": "string"}}}},
    {"name": "os_processes", "title": "Top processes",
     "annotations": {"readOnlyHint": True, "destructiveHint": False},
     "description": "Top processes by `by`=cpu|mem (default cpu), `limit` rows (default 15), optional `pattern` substring. Read-only (use os_service kill / a guarded path to act).",
     "inputSchema": {"type": "object", "properties": {"by": {"type": "string", "enum": ["cpu", "mem"]}, "limit": {"type": "number"}, "pattern": {"type": "string"}}}},
    {"name": "os_power", "title": "Power control",
     "annotations": {"readOnlyHint": False, "destructiveHint": True},
     "description": "Power state via logind/systemd: suspend|hibernate|hybrid-sleep|suspend-then-hibernate|reboot|poweroff|halt. IRREVERSIBLE/DISRUPTIVE — refused unless confirm=true. Uses sudo -n when not root.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["suspend", "hibernate", "hybrid-sleep", "suspend-then-hibernate", "reboot", "poweroff", "halt"]}, "confirm": {"type": "boolean"}}, "required": ["action"]}},
    {"name": "os_notify", "title": "Desktop notification",
     "annotations": {"readOnlyHint": False, "destructiveHint": False},
     "description": "Send a desktop notification to the logged-in user via the session bus (notify-send, else gdbus → org.freedesktop.Notifications). `summary` (title), `body`, `urgency`=low|normal|critical. The agent's way to get the human's attention.",
     "inputSchema": {"type": "object", "properties": {"summary": {"type": "string"}, "body": {"type": "string"}, "urgency": {"type": "string", "enum": ["low", "normal", "critical"]}}, "required": ["summary"]}},
    {"name": "os_dbus", "title": "D-Bus",
     "annotations": {"readOnlyHint": False, "destructiveHint": True},
     "description": "Sanctioned D-Bus access via busctl. op=list (bus names) | tree (object tree of a `service`) | introspect (`service`+`path`) | call (`service`,`path`,`interface`,`member`, optional `signature`+`args`). `scope`=system|user (default system). op=call has side effects → refused unless force=true. list/tree/introspect are read-only.",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string", "enum": ["list", "tree", "introspect", "call"]}, "service": {"type": "string"}, "path": {"type": "string"}, "interface": {"type": "string"}, "member": {"type": "string"}, "signature": {"type": "string"}, "args": {"type": "array"}, "scope": _SCOPE, "force": {"type": "boolean"}}}},
    {"name": "os_reload", "title": "Reload server",
     "annotations": {"readOnlyHint": False, "destructiveHint": True},
     "description": "Hot-reload this server's own code in place (execv) so edits + new tools take effect with no /mcp reconnect.",
     "inputSchema": {"type": "object", "properties": {}}},
]

HANDLERS = {
    "os_diag": h_diag,
    "os_services": h_services,
    "os_service": h_service,
    "os_journal": h_journal,
    "os_resources": h_resources,
    "os_processes": h_processes,
    "os_power": h_power,
    "os_notify": h_notify,
    "os_dbus": h_dbus,
    "os_reload": h_reload,
}

INSTRUCTIONS = (
    "Drive the Linux host through its sanctioned interfaces — systemd, logind, "
    "journald, D-Bus — instead of raw PID hacks. Loop: os_diag (health) → "
    "os_services/os_journal/os_resources (observe) → os_service/os_power/os_dbus "
    "(act). The self-preservation guard refuses severing actions on units the agent "
    "stands on (dbus/logind/sshd/network/tailscaled/session/goosed) unless force=true; "
    "os_power needs confirm=true; os_dbus call needs force=true. System mutations need "
    "root or polkit (sudo -n when not root). Pair with screen-mcp (GUI), NATS, and A2A "
    "for a full-stack agent."
)

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
