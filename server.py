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

__version__ = "2026.6.25"

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
# Human-In-the-Loop — a HUMAN approves destructive actions via MCP elicitation,
# not the model self-setting force/confirm. The flags are only the fallback for
# clients with no elicitation channel.
# --------------------------------------------------------------------------- #
ELICIT_OK = False  # set True at initialize when the client declares elicitation capability
REQUIRE_HUMAN = os.environ.get("OSCTL_REQUIRE_HUMAN") == "1"
_elicit_seq = [9000]

def elicit(message):
    """Ask the human via MCP elicitation/create. Returns 'accept' | 'decline' |
    'cancel', or None when the client has no elicitation capability."""
    if not ELICIT_OK:
        return None
    _elicit_seq[0] += 1
    rid = f"osctl-elicit-{_elicit_seq[0]}"
    req = {"jsonrpc": "2.0", "id": rid, "method": "elicitation/create",
           "params": {"message": message,
                      "requestedSchema": {"type": "object", "required": ["approve"],
                          "properties": {"approve": {"type": "boolean",
                              "description": "Approve this action on the host?"}}}}}
    sys.stdout.write(json.dumps(req) + "\n"); sys.stdout.flush()
    while True:
        line = sys.stdin.readline()
        if not line:
            return "cancel"
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if msg.get("id") != rid:
            continue  # ignore anything that isn't our elicitation response
        if "error" in msg:
            return "decline"
        res = msg.get("result") or {}
        if res.get("action") == "accept":
            return "accept" if (res.get("content") or {}).get("approve", True) else "decline"
        return res.get("action") or "decline"

def approval_path():
    return "human" if ELICIT_OK else ("require-human" if REQUIRE_HUMAN else "flag")

def require_human(desc, flag_name, args, *, floor=False, headless_allow=False):
    """The HIL gate. Returns None to proceed, or an err(...) to block.
      * hard floor      -> always blocked (no override).
      * elicitation on  -> the HUMAN decides; the model's flag is IGNORED (authority).
      * elicitation off -> OSCTL_REQUIRE_HUMAN blocks; else proceed if headless_allow
                           (low self-risk, nobody to ask) or the flag is set."""
    if floor:
        return err(f"REFUSED (hard floor): {desc}. Never permitted — not bypassable.")
    verdict = elicit(f"os-control-mcp needs human approval:\n\n  {desc}\n\nApprove?")
    if verdict == "accept":
        return None
    if verdict in ("decline", "cancel"):
        return err(f"DENIED by human: {desc}")
    # verdict is None -> client has no elicitation channel
    if REQUIRE_HUMAN:
        return err(f"REFUSED: {desc} requires human approval, but this client has no MCP "
                   f"elicitation capability and OSCTL_REQUIRE_HUMAN=1 (no flag fallback).")
    if headless_allow:
        return None
    if args.get(flag_name):
        return None
    return err(f"REFUSED: {desc}. No human-approval channel (client lacks MCP elicitation); "
               f"pass {flag_name}=true to proceed, or dry_run=true to preview.")

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
    sever = action in SEVERING_ACTIONS
    for unit in units:                       # hard floor first — never overridable
        if sever and is_floor(unit):
            return err(f"REFUSED (hard floor): `{action} {unit}` would sever the agent's "
                       f"absolute substrate. Never permitted — not bypassable.")
    base, gerr = _sc(scope, True)
    if gerr:
        return err(gerr)
    cmd = base + [action] + units
    if dry:
        return ok(f"DRY RUN — would execute:\n  {_cmdstr(cmd)}")
    if sever:                                # HIL gate on every severing action
        protected = any(is_protected(u) for u in units)
        risk = ("a unit the agent DEPENDS ON (bus/session/network/remote access)"
                if protected else "a running service")
        block = require_human(f"{action} {' '.join(units)} on {os.uname().nodename} — {risk}",
                              "force", a, headless_allow=not protected)
        if block:
            return block
    rc, out, e = run(cmd, timeout=60)
    msg = (out + e).strip()
    audit("os_service", {"action": action, "units": units, "scope": scope,
                         "approval": approval_path()}, f"rc={rc} {msg[:160]}")
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
    op = a.get("op")  # default/None -> sockets (back-compat with summary/listening/pattern)
    if op in (None, "sockets"):
        if not have("ss"):
            return err("ss not available (install iproute2)")
        if a.get("summary"):
            rc, out, e = run(["ss", "-s"], 10)
            return ok(_trunc(out.strip() or e.strip()))
        flags = "-tulpn" if a.get("listening", True) else "-tanp"
        rc, out, e = run(["ss", flags], 12)
        rows = out.splitlines()
        pat = a.get("pattern")
        if pat and rows:
            rows = [rows[0]] + [r for r in rows[1:] if pat.lower() in r.lower()]
        return ok(_trunc("\n".join(rows) or e.strip() or "no sockets"))
    if op in ("addr", "links", "routes"):
        if not have("ip"):
            return err("ip not available (install iproute2)")
        argv = {"addr": ["-br", "addr"], "links": ["-br", "link"], "routes": ["route"]}[op]
        rc, out, e = run(["ip"] + argv, 8)
        return ok(_trunc(out.strip() or e.strip() or "(none)"))
    if op == "wifi":
        if have("nmcli"):
            rc, out, e = run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "dev", "wifi"], 10)
            return ok(_trunc(out.strip() or e.strip() or "no wifi networks"))
        if have("iw"):
            rc, out, e = run(["iw", "dev"], 10)
            return ok(_trunc(out.strip() or e.strip()))
        return err("no wifi tool (nmcli or iw)")
    if op == "nm":
        if not have("nmcli"):
            return err("nmcli not available (NetworkManager)")
        rc, d, _ = run(["nmcli", "-t", "device", "status"], 10)
        rc2, c, _ = run(["nmcli", "-t", "connection", "show", "--active"], 10)
        return ok(_trunc((d.strip() + "\n--- active connections ---\n" + c.strip()).strip()))
    return err("op must be sockets|addr|links|routes|wifi|nm")

# --------------------------------------------------------------------------- #
# observe: containers / disk / hardware  (read-only; from real-session log mining)
# --------------------------------------------------------------------------- #
def _container_cli(pref=None):
    for c in ([pref] if pref else []) + ["docker", "podman"]:
        if c and have(c):
            return c
    return None

def h_containers(a):
    cli = _container_cli(a.get("engine"))
    if not cli:
        return err("no container engine found (docker or podman)")
    op = a.get("op", "ps")
    name = a.get("name")
    if op == "ps":
        cmd = [cli, "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"]
        if a.get("all"):
            cmd.insert(2, "-a")
        rc, out, e = run(cmd, 15)
        return ok(_trunc(out.strip() or e.strip() or "no containers"))
    if op == "logs":
        if not name:
            return err("op=logs needs `name`")
        cmd = [cli, "logs", "--tail", str(int(a.get("lines", 60)))]
        if a.get("since"):
            cmd += ["--since", a["since"]]
        cmd.append(name)
        rc, out, e = run(cmd, 25)
        return ok(_trunc((out + e).strip() or "(no logs)"))
    if op == "inspect":
        if not name:
            return err("op=inspect needs `name`")
        rc, out, e = run([cli, "inspect", name], 15)
        return ok(_trunc(out.strip() or e.strip()))
    if op == "stats":
        rc, out, e = run([cli, "stats", "--no-stream", "--format",
                          "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"], 25)
        return ok(_trunc(out.strip() or e.strip() or "no running containers"))
    if op == "images":
        rc, out, e = run([cli, "images", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}"], 15)
        return ok(_trunc(out.strip() or e.strip() or "no images"))
    if op == "compose":
        if cli != "docker":
            return err("op=compose needs docker")
        rc, out, e = run(["docker", "compose", "ls"], 15)
        return ok(_trunc(out.strip() or e.strip() or "no compose projects"))
    return err("op must be ps|logs|inspect|stats|images|compose")

def _human_bytes(n):
    for u in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.0f}P"

def h_disk(a):
    op = a.get("op", "usage")
    if op == "usage":
        rc, out, e = run(["df", "-h", "--output=source,fstype,size,used,avail,pcent,target",
                          "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs"], 8)
        return ok(_trunc(out.strip() or e.strip()))
    if op == "du":
        path = a.get("path") or "."
        rc, out, e = run(["du", "-B1", "--max-depth", str(int(a.get("depth", 1))), path], 45)
        if rc != 0 and not out:
            return err(f"du failed: {e.strip()}")
        items = []
        for ln in out.splitlines():
            try:
                b, p = ln.split("\t", 1)
                items.append((int(b), p))
            except Exception:
                pass
        items.sort(reverse=True)
        rows = [f"{_human_bytes(b):>7}  {p}" for b, p in items[:int(a.get("limit", 20))]]
        return ok(_trunc("\n".join(rows) or "(empty)"))
    if op == "blocks":
        if not have("lsblk"):
            return err("lsblk not available (util-linux)")
        rc, out, e = run(["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL"], 10)
        return ok(_trunc(out.strip() or e.strip()))
    if op == "mounts":
        if have("findmnt"):
            rc, out, e = run(["findmnt", "--real", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS"], 10)
        else:
            rc, out, e = run(["mount"], 10)
        return ok(_trunc(out.strip() or e.strip()))
    return err("op must be usage|du|blocks|mounts")

def h_hardware(a):
    op = a.get("op", "summary")
    if op == "cpu":
        rc, out, e = run(["lscpu"], 8)
        return ok(_trunc(out.strip() or e.strip()))
    if op == "pci":
        if not have("lspci"):
            return err("lspci not available (pciutils)")
        rc, out, e = run(["lspci"] + (["-nnk"] if a.get("verbose") else []), 10)
        return ok(_trunc(out.strip() or e.strip()))
    if op == "usb":
        if not have("lsusb"):
            return err("lsusb not available (usbutils)")
        rc, out, e = run(["lsusb"], 10)
        return ok(_trunc(out.strip() or e.strip()))
    if op == "gpu":
        out = []
        if have("nvidia-smi"):
            rc, o, _ = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,"
                            "utilization.gpu,temperature.gpu", "--format=csv,noheader"], 10)
            if rc == 0 and o.strip():
                out.append("NVIDIA: " + "; ".join(o.split("\n")))
        if have("lspci"):
            rc, o, _ = run(["lspci"], 10)
            vid = [l for l in o.splitlines() if any(k in l for k in ("VGA", "3D", "Display"))]
            if vid:
                out.append("PCI display:\n" + "\n".join(vid))
        cards = [os.path.basename(c) for c in sorted(glob.glob("/sys/class/drm/card[0-9]"))]
        if cards:
            out.append("DRM cards: " + ", ".join(cards))
        return ok(_trunc("\n\n".join(out) or "no GPU info"))
    # summary
    out = []
    rc, o, _ = run(["uname", "-srm"], 5)
    out.append(o.strip())
    if have("lscpu"):
        rc, o, _ = run(["lscpu"], 8)
        keep = ("Model name", "Architecture", "CPU(s)", "Socket(s)",
                "Core(s) per socket", "Thread(s) per core")
        out.append("\n".join(l for l in o.splitlines() if l.split(":")[0].strip() in keep))
    rc, o, _ = run(["free", "-h"], 5)
    if o:
        mem = [l for l in o.splitlines() if l.lower().startswith("mem")]
        out += mem
    return ok(_trunc("\n".join(x for x in out if x)))

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
    cap = ""
    if have("systemctl"):
        verb = {"poweroff": "can-poweroff", "reboot": "can-reboot", "suspend": "can-suspend",
                "hibernate": "can-hibernate", "halt": "can-halt"}.get(action)
        if verb:
            _, co, _ = run(["systemctl", verb], 6)
            cap = f" [{verb}: {co.strip() or 'unknown'}]"
    block = require_human(f"{action} {os.uname().nodename} — IRREVERSIBLE/disruptive{cap}",
                          "confirm", a)
    if block:
        return block
    rc, out, e = run(cmd, 20)
    audit("os_power", {"action": action, "approval": approval_path()}, f"rc={rc} {(out + e).strip()[:120]}")
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
        need_val, argv = set_map[op]
        is_write = op.startswith("set-")
        val = a.get("value")
        if need_val and val is None:
            return err(f"op={op} needs `value`")
        if not is_write:                       # list-* etc. are read-only — no gate
            rc, out, e = run([binary] + argv, 12)
            return ok(_trunc(out.strip() or e.strip() or "(empty)"))
        base, gerr = _priv_prefix(binary, True)
        if gerr:
            return err(gerr)
        cmd = base + argv + ([str(val)] if need_val else [])
        if bool(a.get("dry_run")):
            return ok(f"DRY RUN — would execute:\n  {_cmdstr(cmd)}")
        block = require_human(f"set machine {label}: {op} {val if need_val else ''}".strip(),
                              "force", a)
        if block:
            return block
        rc, out, e = run(cmd, 15)
        audit(f"os_{label}", {"op": op, "value": val, "approval": approval_path()},
              f"rc={rc} {(out + e).strip()[:120]}")
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
        block = require_human(f"D-Bus {op} {a['service']} {a['member']} (side effects)", "force", a)
        if block:
            return block
        rc, out, e = run(cmd, tmo)
        audit("os_dbus", {"op": op, "service": a["service"], "member": a["member"],
                          "approval": approval_path()}, f"rc={rc} {(out + e).strip()[:120]}")
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
                                 "timedatectl", "hostnamectl", "localectl", "ss", "ip",
                                 "lsblk", "lspci", "docker", "podman", "nvidia-smi",
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
    hil = ("human-in-the-loop via MCP elicitation" if ELICIT_OK
           else ("REFUSE (OSCTL_REQUIRE_HUMAN=1, no elicitation)" if REQUIRE_HUMAN
                 else "flag fallback (client has no elicitation capability)"))
    lines.append(f"gating: hard floor {len(CRITICAL_FLOOR)} units (never severable) + "
                 f"{len(PROTECTED_TOKENS)} protected tokens; approval = {hil}")
    lines.append(f"audit -> {_state_dir()}/audit.jsonl")
    lines.append("ethics: enforces The Agent Oath §1,2,3,5,7,11 (human welfare > task; "
                 "human agency/oversight via HIL) — https://theagentoath.com")
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
    {"name": "os_net", "title": "Network", "annotations": _RO,
     "description": "Network state (read-only). op=sockets (default; ss -tulpn listening + owning PID; summary=true→ss -s; listening=false→all; `pattern`) | addr (ip -br addr) | links (ip -br link) | routes (ip route) | wifi (nmcli/iw scan) | nm (NetworkManager device + active connections).",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string", "enum": ["sockets", "addr", "links", "routes", "wifi", "nm"]}, "summary": {"type": "boolean"}, "listening": {"type": "boolean"}, "pattern": {"type": "string"}}}},
    {"name": "os_sensors", "title": "Thermal sensors", "annotations": _RO,
     "description": "Temperatures from /sys/class/thermal/thermal_zone*. full=true also runs `sensors` (lm_sensors) if present. Read-only.",
     "inputSchema": {"type": "object", "properties": {"full": {"type": "boolean"}}}},
    {"name": "os_containers", "title": "Containers", "annotations": _RO,
     "description": "Inspect Docker/Podman (read-only — the AI's most-used host observation). op=ps (default; all=true for stopped) | logs (`name`, lines, since) | inspect (`name`) | stats (live cpu/mem/net/io) | images | compose (docker compose ls). `engine`=docker|podman (auto-detected).",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string", "enum": ["ps", "logs", "inspect", "stats", "images", "compose"]}, "name": {"type": "string"}, "all": {"type": "boolean"}, "lines": {"type": "number"}, "since": {"type": "string"}, "engine": {"type": "string", "enum": ["docker", "podman"]}}}},
    {"name": "os_disk", "title": "Storage", "annotations": _RO,
     "description": "Storage (read-only). op=usage (default; df -h real fs) | du (largest dirs under `path`, `depth`, `limit` — sorted) | blocks (lsblk: devices/partitions/fs/mounts) | mounts (findmnt real mounts).",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string", "enum": ["usage", "du", "blocks", "mounts"]}, "path": {"type": "string"}, "depth": {"type": "number"}, "limit": {"type": "number"}}}},
    {"name": "os_hardware", "title": "Hardware / GPU", "annotations": _RO,
     "description": "Hardware inventory (read-only). op=summary (default; kernel + cpu model + memory) | cpu (lscpu) | pci (lspci; verbose=true→-nnk drivers) | usb (lsusb) | gpu (nvidia-smi + PCI display + /sys/class/drm cards).",
     "inputSchema": {"type": "object", "properties": {"op": {"type": "string", "enum": ["summary", "cpu", "pci", "usb", "gpu"]}, "verbose": {"type": "boolean"}}}},
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
    "os_containers": h_containers, "os_disk": h_disk, "os_hardware": h_hardware,
    "os_power": h_power, "os_session": h_session, "os_time": h_time, "os_hostname": h_hostname,
    "os_locale": h_locale, "os_notify": h_notify, "os_dbus": h_dbus, "os_reload": h_reload,
}

INSTRUCTIONS = (
    "Drive the Linux host through its sanctioned interfaces — systemd, logind, journald, D-Bus, "
    "machine settings, containers — not raw PID hacks. Loop: os_diag (health) → observe "
    "(os_services/os_journal/os_resources/os_processes/os_pressure/os_net/os_disk/os_hardware/"
    "os_containers/os_sensors/os_session) → act (os_service/os_power/os_dbus/os_time/os_hostname/"
    "os_locale/os_notify) → os_wait / re-read (confirm). HUMAN-IN-THE-LOOP gating: destructive "
    "actions (severing a service, power, D-Bus/machine writes) ask a HUMAN for approval via MCP "
    "elicitation when your client supports it — you do NOT decide; the human does. If the client "
    "has no elicitation channel, the force/confirm flags are the fallback (set OSCTL_REQUIRE_HUMAN=1 "
    "to forbid even that). A hard floor refuses severing dbus/logind/init ALWAYS — never bypassable. "
    "Any mutation accepts dry_run=true to preview the exact command. System mutations need root/polkit "
    "(sudo -n when not root). Every mutation is audit-logged. This server is a reference ENFORCER of "
    "The Agent Oath (theagentoath.com) — human welfare over task completion, human agency and oversight "
    "preserved via the HIL gate; the operator's gating is the authority. Works with any MCP client. "
    "Pairs with screen-mcp (GUI), NATS, and A2A for a full-stack agent."
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
    # readline() loop (not `for line in sys.stdin`) so elicit() can read the
    # human's response from stdin without the iterator's read-ahead swallowing it.
    while True:
        line = sys.stdin.readline()
        if not line:
            break
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
            global ELICIT_OK
            client_caps = (msg.get("params") or {}).get("capabilities") or {}
            ELICIT_OK = "elicitation" in client_caps
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
