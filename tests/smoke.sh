#!/usr/bin/env bash
# Smoke test: drive the stdio server via the real plugin launcher wire path
# (bin/os-control-mcp → scripts/run-python.sh → server.py) and assert MCP
# tools/list + os_diag. No systemd required — os_diag reports what's missing.
# Run from repo root: bash tests/smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."

ROOT="$(pwd)"
export CLAUDE_PLUGIN_ROOT="$ROOT"

LAUNCHER="$ROOT/bin/os-control-mcp"
test -x "$LAUNCHER"

# Drive MCP over the production launcher (same path plugin.json mcpServers uses).
OUT="$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"os_diag","arguments":{}}}' \
  | bash "$LAUNCHER")"

OUT="$OUT" bash scripts/run-python.sh -c '
import os, json
by = {}
for l in os.environ["OUT"].splitlines():
    l = l.strip()
    if not l:
        continue
    m = json.loads(l)
    if m.get("id") is not None:
        by[m["id"]] = m
assert by[1]["result"]["serverInfo"]["name"] == "os-control-mcp", "bad serverInfo"
assert by[1]["result"].get("protocolVersion") == "2025-11-25", by[1]["result"]
tools = by[2]["result"]["tools"]
names = [t["name"] for t in tools]
assert "os_diag" in names and "os_service" in names and "os_power" in names, names
assert len(tools) >= 8, f"too few tools: {names}"
missing = []
for t in tools:
    n = t.get("name")
    if not t.get("title"):
        missing.append(f"{n}:title")
    if "annotations" not in t or "readOnlyHint" not in t.get("annotations", {}):
        missing.append(f"{n}:annotations")
assert not missing, f"tools/list missing title/annotations: {missing}"
assert by[3]["result"]["content"][0]["text"].startswith("os-control-mcp"), "os_diag broken"
print(f"smoke OK - {len(tools)} tools: {chr(44).join(names)}")
'

echo "== run-python launcher (GOLD) =="
test -f scripts/run-python.sh
bash -n scripts/run-python.sh
bash -n bin/os-control-mcp
grep -q 'run-python.sh' bin/os-control-mcp
# no bare python as mcp command in manifests
! grep -qE '"command"[[:space:]]*:[[:space:]]*"python3?"' .claude-plugin/plugin.json
bash scripts/run-python.sh -c 'import sys; assert sys.version_info >= (3, 10)'
# thin PATH — simulates Claude GUI spawn (Homebrew/pyenv off PATH)
THIN="$(env -i HOME="$HOME" PATH="/usr/bin:/bin" bash scripts/run-python.sh -c 'import sys; print(sys.version_info[0])' 2>/dev/null || true)"
if [ "$THIN" = "3" ]; then
  echo "  ok: run-python thin PATH"
elif [ -x /usr/bin/python3 ] || [ -x /opt/homebrew/bin/python3 ]; then
  echo "  FAIL: run-python thin PATH (got: ${THIN:-empty})" >&2
  exit 1
else
  echo "  ok: run-python thin PATH skipped (no system python3 on this host)"
fi
echo "  ok: run-python"
