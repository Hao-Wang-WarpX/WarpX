"""stdin JSON-RPC 启动测试: 用 init + tools/list 验证 MCP server 真的能通讯."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 项目根 (本文件在 tests/)

# init request (MCP 协议)
init_req = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "smoke-test", "version": "0.0.1"},
    },
}

tools_req = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {},
}

# 把两个请求连成 NDJSON (MCP 用换行分隔 JSON-RPC 消息)
payload = json.dumps(init_req) + "\n" + json.dumps(tools_req) + "\n"

VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
print(f"using: {VENV_PY} (exists: {VENV_PY.exists()})")

proc = subprocess.run(
    [str(VENV_PY), "-m", "web_mcp"],
    cwd=str(ROOT),
    input=payload,
    capture_output=True,
    encoding="utf-8",
    errors="replace",
    timeout=15,
)

# stdout 可能因 stdout buffering 不及时刷出来, 用 stderr 看异常
print("=== STDERR ===")
print(proc.stderr[:2000])
print("=== STDOUT (head 3000 chars) ===")
stdout = proc.stdout or ""
print(stdout[:3000])
print(f"=== exit: {proc.returncode} ===")

# 检查 tools/list 返回的 4 个工具名
if not stdout:
    print("\nFAIL - empty stdout (server crashed)")
    sys.exit(1)
out_lines = [ln for ln in stdout.split("\n") if ln.strip()]
if len(out_lines) >= 2:
    try:
        tools_resp = json.loads(out_lines[1])
        tools = tools_resp.get("result", {}).get("tools", [])
        names = sorted(t.get("name") for t in tools)
        expected = sorted(["web_search", "fetch_url", "download_image", "search_and_fetch"])
        if names == expected:
            print(f"\nOK - all 4 tools registered: {names}")
            print("\nTool schemas:")
            for t in tools:
                print(f"  - {t.get('name'):18s} - {t.get('description', '')[:70]}")
            sys.exit(0)
        else:
            print(f"\nFAIL - expected {expected}, got {names}")
            sys.exit(1)
    except Exception as e:
        print(f"\nFAIL - parse error: {e}")
        print(f"out_lines[1]={out_lines[1][:500] if len(out_lines) > 1 else 'N/A'}")
        sys.exit(1)
else:
    print(f"\nFAIL - only {len(out_lines)} stdout lines")
    sys.exit(1)