#!/usr/bin/env bash
# Smoke test: drive the stdio server and assert it speaks MCP and lists its tools.
# Pure-stdlib server, so this runs anywhere python3 exists (no systemd needed —
# os_diag just reports what's missing). Run from repo root: bash tests/smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"os_diag","arguments":{}}}' \
  | python3 server.py)"

OUT="$OUT" python3 -c '
import os, json
by = {}
for l in os.environ["OUT"].splitlines():
    l = l.strip()
    if not l:
        continue
    m = json.loads(l)                       # must be valid JSON-RPC
    if m.get("id") is not None:
        by[m["id"]] = m
assert by[1]["result"]["serverInfo"]["name"] == "os-control-mcp", "bad serverInfo"
tools = [t["name"] for t in by[2]["result"]["tools"]]
assert "os_diag" in tools and "os_service" in tools and "os_power" in tools, tools
assert len(tools) >= 8, f"too few tools: {tools}"
assert by[3]["result"]["content"][0]["text"].startswith("os-control-mcp"), "os_diag broken"
print(f"smoke OK - {len(tools)} tools: {chr(44).join(tools)}")
'

echo "== run-python launcher =="
test -f scripts/run-python.sh
bash -n scripts/run-python.sh
bash -n bin/os-control-mcp
grep -q 'run-python.sh' bin/os-control-mcp
bash scripts/run-python.sh -c 'import sys; assert sys.version_info >= (3, 10)'
echo "  ok: run-python"
