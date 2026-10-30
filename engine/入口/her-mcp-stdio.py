#!/usr/bin/env python3
"""her-mcp-stdio：同三个只读工具的 **stdio 版** MCP（给"本地起进程"的客户端用）。

跟 her-mcp-http.py 暴露同样的能力：
  · search_notes  查触动 / 项目思考
  · recall        查 L3 永久记忆（时间 + 关键词 → 片段）
  · get_snapshot  读任务看板
**写能力一律不暴露**（handoff / 笔记 / 改任务看板只归 DSH 的完整模式）。

用法（客户端里通常这样填）：
  command: <python.exe 完整路径>
  args:    {ENGINE_ROOT}\\入口\\her-mcp-stdio.py

手动测：
  echo {"jsonrpc":"2.0","id":1,"method":"tools/list"} | python 入口/her-mcp-stdio.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 复用 HTTP 版里的工具定义与调用实现（那份不依赖网络）
import importlib.util

spec = importlib.util.spec_from_file_location("hermcp", HERE / "her-mcp-http.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TOOLS = mod.TOOLS
call_tool = mod.call_tool
PROTOCOL = mod.PROTOCOL
SERVER_INFO = mod.SERVER_INFO


def handle(req):
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        p = req.get("params") or {}
        try:
            text = call_tool(p.get("name"), p.get("arguments"))
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": f"调用失败：{e}"}], "isError": True}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        out = handle(req)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    main()
