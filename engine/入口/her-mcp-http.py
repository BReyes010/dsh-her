#!/usr/bin/env python3
"""her-mcp-http：把她的工具用 HTTP 暴露成 MCP（给千问桌面版这类客户端接）。

只暴露**读**能力，且只给本机：
  · search_notes  查触动 / 项目思考（她自己的两份笔记）
  · recall        查 L3 永久记忆（时间 + 关键词 → 片段）
  · get_snapshot  读任务看板（交接单）
写能力（handoff/笔记/任务看板）**一律不暴露**——那些只归 DSH 里的完整模式。

协议：MCP over HTTP（JSON-RPC 2.0）
  POST /mcp        单条或批量 JSON-RPC
  GET  /health     活着没
鉴权：`?token=<token>` 或 `Authorization: Bearer <token>`
      token 在启动时生成/读取 `入口/mcp-token.txt`（本机文件）

用法：
  python 入口/her-mcp-http.py --port 8790
  python 入口/her-mcp-http.py --port 8790 --print-url   # 打印带 token 的地址
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "脚本"
TOKEN_FILE = ROOT / "入口" / "mcp-token.txt"
PROTOCOL = "2024-11-05"
SERVER_INFO = {"name": "her-mcp", "version": "0.1.0"}

SESSIONS = {}          # sid -> {"q": list, "cv": Condition}
SESS_LOCK = __import__("threading").Lock()

TOOLS = [
    {
        "name": "search_notes",
        "description": "查她自己的两份笔记：触动（打动过她的话）与项目（大项目思路）。"
                       "按关键词查，只返回命中片段。",
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["触动", "项目"]},
            "query": {"type": "string"}}, "required": ["kind", "query"]},
    },
    {
        "name": "recall",
        "description": "查 L3 永久记忆（原始日志）：用「大致时间 + 关键词」找回很久以前发生过什么。"
                       "只回 ≤3 条片段（每条 ≤300 字，带出处），查不到会说没找到。",
        "inputSchema": {"type": "object", "properties": {
            "question": {"type": "string"},
            "when": {"type": "string", "description": "上周 / 10 月中 / 最近三天 / 第 2 期"},
            "keywords": {"type": "string"}}, "required": ["question"]},
    },
    {
        "name": "get_card",
        "description": "读一张**技能卡/任务卡**的正文（只读，不算装配）。"
                       "外部客户端里想用某个能力时用它，把正文读进上下文。"
                       "素材类（kind: memory）不从这里走——那些让她自己查记忆。",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "string", "description": "卡 id 或名字，如 口播 / 维修 / 口播交付"}},
            "required": ["id"]},
    },
    {
        "name": "get_snapshot",
        "description": "读任务看板交接单（现在 / 卡在哪 / 下一步）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def py(*args, timeout=60):
    p = subprocess.run([sys.executable, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return (p.stdout or "").strip() or (p.stderr or "").strip()


def child_tasks_mcp(parent_id):
    """父卡下的子卡（kind: task 且 skill: parent_id）：正文 + 汉字数。"""
    out = []
    for p in sorted((ROOT / "skills").glob("*.md")):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8")
        m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", t, re.S)
        if not m:
            continue
        fm, body = m.group(1), m.group(2)
        def g(k):
            mm = re.search(rf"^{k}:\s*(.+)$", fm, re.M)
            return mm.group(1).strip().strip("[]") if mm else ""
        if g("kind") == "task" and g("skill") == parent_id:
            b = "\n".join(l for l in body.splitlines() if not l.strip().startswith("<!--")).strip()
            out.append((g("id") or p.stem, b, sum(1 for ch in b if "\u4e00" <= ch <= "\u9fff")))
    return out


def get_card_text(cid):
    """读卡正文：只认 skill / task；memory 类拒（素材自己去查记忆）。"""
    import re
    skills = ROOT / "skills"
    if not skills.exists():
        return "没有 skills 目录"
    hit = None
    for p in sorted(skills.glob("*.md")):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", t, re.S)
        fm = m.group(1) if m else ""
        body = m.group(2) if m else t
        kid = re.search(r"^id:\s*(.+)$", fm, re.M)
        kname = re.search(r"^name:\s*(.+)$", fm, re.M)
        kind = re.search(r"^kind:\s*(.+)$", fm, re.M)
        ids = {p.stem}
        if kid:
            ids.add(kid.group(1).strip())
        if kname:
            ids.add(kname.group(1).strip())
        if cid in ids:
            hit = (p, kind.group(1).strip() if kind else "", body.strip())
            break
    if not hit:
        have = "、".join(sorted(p.stem for p in skills.glob("*.md") if not p.name.startswith("_")))
        return f"没有这张卡：{cid}。现有：{have}"
    p, kind, body = hit
    if kind == "task":
        parent = ""
        try:
            mm = re.search(r"^skill:\s*(.+)$", fm, re.M)
            parent = mm.group(1).strip() if mm else ""
        except Exception:
            pass
        return (f"「{cid}」是子卡（任务卡）：子卡要跟着父卡读，"
                + (f"用 get_card(\"{parent}\") 会一并给出。" if parent else "请先读它的父卡。"))
    if kind == "memory":
        return (f"「{cid}」是素材卡（kind: memory），不通过 get_card 加载——"
                "需要素材时自己去查记忆（search_memory / recall）。")
    body = "\n".join(l for l in body.splitlines() if not l.strip().startswith("<!--"))
    han = sum(1 for ch in body if "\u4e00" <= ch <= "\u9fff")
    head = (f"【{cid}】kind={kind}｜{han} 汉字（这是**正文**，不是装配；"
            f"用完之后在收尾时说明用过它）")
    kids = child_tasks_mcp(cid) if kind == "skill" else []
    for kid, kidbody, khan in kids:
        head += (f"\n\n## 子卡：{kid}（任务流程，跟着父卡一起读）｜{khan} 汉字\n\n{kidbody}")
    if kids:
        head += "\n\n（子卡不用单独要——跟着父卡一起来的）"
    return head + "\n\n" + body


def call_tool(name, args):
    args = args or {}
    if name == "search_notes":
        kind = args.get("kind") or "触动"
        q = (args.get("query") or "").strip()
        if not q:
            return "query 不能为空"
        return py(str(SCRIPTS / "l3.py"), "grep", q, "--kind", kind, "--audit", "--limit", "5")
    if name == "recall":
        q = (args.get("question") or "").strip()
        if not q:
            return "question 不能为空"
        cmd = [str(SCRIPTS / "l3q.py"), "recall", "--question", q]
        if args.get("when"):
            cmd += ["--when", args["when"]]
        if args.get("keywords"):
            cmd += ["--keywords", args["keywords"]]
        return py(*cmd)
    if name == "get_card":
        cid = (args.get("id") or "").strip()
        if not cid:
            return "id 不能为空"
        return get_card_text(cid)
    if name == "get_snapshot":
        return py(str(ROOT / "入口" / "her-host.py"), "--print", "完整", "--desk", "full")[:4000] or "（读不到任务看板）"
    return f"没有工具 {name}"


def handle_rpc(req):
    """单条 JSON-RPC → 响应 dict（通知返回 None）。"""
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        want = ((req.get("params") or {}).get("protocolVersion") or "").strip()
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": want or PROTOCOL,
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
        params = req.get("params") or {}
        name = params.get("name")
        try:
            text = call_tool(name, params.get("arguments"))
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": text}], "isError": False}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"调用失败：{e}"}], "isError": True}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


class Handler(BaseHTTPRequestHandler):
    server_version = "her-mcp/0.1"
    token = ""

    def log_message(self, fmt, *args):
        sys.stderr.write("[her-mcp] " + (fmt % args) + "\n")

    def _auth(self):
        q = parse_qs(urlparse(self.path).query)
        if q.get("token", [""])[0] == self.token:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {self.token}"

    def _send(self, code, payload, sse=False):
        if sse:
            # Streamable HTTP：SSE 内容里必须是 event/data 帧，不是裸 JSON
            items = payload if isinstance(payload, list) else [payload]
            parts = []
            for it in items:
                parts.append("event: message\ndata: "
                             + json.dumps(it, ensure_ascii=False) + "\n\n")
            body = "".join(parts).encode("utf-8")
            ctype = "text/event-stream; charset=utf-8"
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse_head(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _sse_send(self, event, data):
        self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    def do_GET_sse(self):
        """legacy HTTP+SSE：先发 endpoint，再把工具响应推回来。"""
        import threading
        import uuid
        sid = uuid.uuid4().hex[:12]
        cv = threading.Condition()
        with SESS_LOCK:
            SESSIONS[sid] = {"q": [], "cv": cv}
        try:
            self._sse_head()
            # endpoint 里带上 token：客户端 POST 回来时就不用再拼鉴权
            self._sse_send("endpoint", f"/messages?session={sid}&token={self.token}")
            import time
            last = time.time()
            while True:
                with cv:
                    if not SESSIONS[sid]["q"]:
                        cv.wait(timeout=5)
                    items = SESSIONS[sid]["q"][:]
                    SESSIONS[sid]["q"].clear()
                for item in items:
                    self._sse_send("message", item)
                if time.time() - last > 15:
                    self._sse_send("ping", "{}")
                    last = time.time()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with SESS_LOCK:
                SESSIONS.pop(sid, None)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Accept, Mcp-Session-Id")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, {"ok": True, "server": SERVER_INFO})
        if path in ("/sse", "/mcp"):
            return self.do_GET_sse()
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/messages":
            if not self._auth():
                return self._send(403, {"error": "token 不对"})
            sid = (parse_qs(url.query).get("session") or [""])[0]
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace")
            try:
                req = json.loads(raw)
            except Exception:
                return self._send(400, {"error": "JSON 解析失败"})
            out = handle_rpc(req) if not isinstance(req, list) else [x for x in (handle_rpc(r) for r in req) if x]
            with SESS_LOCK:
                sess = SESSIONS.get(sid)
            if sess is None:
                return self._send(404, {"error": f"没有这个会话：{sid}"})
            with sess["cv"]:
                sess["q"].append(json.dumps(out, ensure_ascii=False))
                sess["cv"].notify_all()
            return self._send(202, {"ok": True})
        if url.path != "/mcp":
            return self._send(404, {"error": "not found"})
        if not self._auth():
            return self._send(403, {"error": "token 不对（地址要带 ?token=…）"})
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        try:
            req = json.loads(raw)
        except Exception:
            return self._send(400, {"error": "JSON 解析失败"})

        sse = "text/event-stream" in (self.headers.get("Accept") or "")
        if isinstance(req, list):
            outs = [x for x in (handle_rpc(r) for r in req) if x]
            return self._send(200, outs, sse)
        out = handle_rpc(req)
        if out is None:
            return self._send(202, {"ok": True})
        self._send(200, out, sse)


def main():
    ap = argparse.ArgumentParser(description="她的 MCP over HTTP（只读）")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--print-url", action="store_true")
    ap.add_argument("--log", default="", help="把日志写到这个文件（隐藏启动时用）")
    a = ap.parse_args()

    if a.log:
        LOG = Path(a.log)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        f = LOG.open("a", encoding="utf-8")
        sys.stdout = f
        sys.stderr = f
        print(f"\n=== start {__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S} ===")

    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    else:
        token = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token, encoding="utf-8")
    Handler.token = token
    url = f"http://{a.host}:{a.port}/mcp?token={token}"
    print(f"URL: {url}", flush=True)
    print(f"TOKEN: {token}", flush=True)
    print(f"[OK] MCP ready on port {a.port}", file=sys.stderr)
    print(f"     token file -> {TOKEN_FILE.name}", file=sys.stderr, flush=True)
    if a.print_url:
        print(url)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    main()
