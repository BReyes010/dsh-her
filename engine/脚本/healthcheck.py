#!/usr/bin/env python3
"""healthcheck —— 全功能体检，**全程在副本里跑**（不碰真实记忆/人格/任务看板）。

设计原则（2026-10-29 修）：
  · 真实引擎只读：开始时给关键目录拍指纹，结束时再拍一次，不一致就报 BUG；
  · 所有命令都在 `.tmp-healthcheck/` 副本里执行（HER_HOME 指副本）；
  · 副本里清掉真实签发，写权用例才有意义；
  · 跑完删副本（--keep 保留）。

用法：python 脚本/healthcheck.py [--keep]
退出码：0 全过 / 1 有失败
"""

def force_rmtree(p):
    """删目录前先解只读（快照里有只读文件，直接 rmtree 会 WinError 5）。"""
    import os as _os
    import shutil as _sh
    import stat as _st
    p = Path(p)
    if not p.exists():
        return
    for root, dirs, files in _os.walk(p):
        for n in files + dirs:
            f = Path(root) / n
            try:
                _os.chmod(f, _st.S_IWRITE | _st.S_IREAD)
            except Exception:
                pass
    _sh.rmtree(p, ignore_errors=True)


import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REAL = Path(__file__).resolve().parent.parent
SB = REAL.parent / ".tmp-healthcheck"
PY = sys.executable

ok, bad = [], []


def rec(name, cond, extra=""):
    (ok if cond else bad).append((name, str(extra)[:200]))
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + ("" if cond else f" — {str(extra)[:160]}"))


def fingerprint():
    """真实引擎的只读指纹：文件数与名单 hash。"""
    out = {}
    for sub in ("记忆/L3/handoff", "记忆/L3", "入口/任务看板.json", "人格"):
        p = REAL / sub
        if p.is_dir():
            items = sorted(x.name for x in p.iterdir() if x.is_file())
            out[sub] = (len(items), hashlib.sha256("".join(items).encode()).hexdigest()[:16])
        elif p.is_file():
            out[sub] = (p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest()[:16])
    return out


def env_of():
    return dict(os.environ, HER_HOME=str(SB), PYTHONIOENCODING="utf-8")


def run(script, *args, timeout=300):
    p = subprocess.run([PY, str(SB / "脚本" / script), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env_of(), timeout=timeout)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def rpc(*calls):
    inp = "\n".join(json.dumps(c, ensure_ascii=False) for c in calls) + "\n"
    p = subprocess.run([PY, str(SB / "入口" / "her-host.py")], input=inp,
                       capture_output=True, text=True, encoding="utf-8", env=env_of())
    out = {}
    for line in (p.stdout or "").splitlines():
        if not line.strip().startswith("{"):
            continue
        try:
            d = json.loads(line)
            res = d.get("result") or {}
            out[d.get("id")] = (res.get("content") or [{}])[0].get("text", json.dumps(res, ensure_ascii=False))
        except Exception:
            continue
    return out


def grant(*args):
    p = subprocess.run([PY, str(SB / "脚本" / "grant.py"), *args],
                       capture_output=True, text=True, encoding="utf-8", env=env_of())
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def hb(i, client="体检"):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": "write_handoff",
                       "arguments": {"client": client,
                                     "content": f"# handoff · 2026-10-29 01:10 · 客户端：{client}\n\n"
                                                f"## 条目 1\n谁做的：她\n做了什么：体检写权\n主题：体检\nopen：false\n"}}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    if SB.exists():
        force_rmtree(SB)
    shutil.copytree(REAL, SB, ignore=shutil.ignore_patterns(
        "备份", "产出", "素材", "出差包", ".tmp*", "__pycache__", "发布", "历史"))
    for p in (SB / "入口" / "_签发.json", SB / "记忆" / "L3" / "_签发.jsonl"):
        if p.exists():
            p.unlink()
    print(f"沙箱：{SB}\n")
    before = fingerprint()

    # 1 工具
    r = rpc({"jsonrpc": "2.0", "id": "t", "method": "tools/list"})
    tools = [t["name"] for t in json.loads(r.get("t") or "{}").get("tools", [])]
    rec("工具数=18", len(tools) == 18, f"{len(tools)}: {tools}")
    for n in ("recall", "search_notes", "note_daily", "note_add", "wb_retire",
          "preflight", "request_card", "unload_card", "l1_candidate"):
        rec(f"工具存在：{n}", n in tools)

    # 2 写权：无签拒 + 留痕
    r = rpc(hb(1))
    rec("无签发写 handoff → 拒", "拒绝" in r.get(1, ""))
    glog = SB / "记忆" / "L3" / "_签发.jsonl"
    g = glog.read_text(encoding="utf-8") if glog.exists() else ""
    rec("attempt 留痕", '"event": "attempt"' in g)
    rec("denied 留痕", '"event": "denied"' in g)

    # 3 只签 memory → handoff 仍拒
    grant("sign", "--tools", "write:memory", "--reason", "体检")
    r = rpc(hb(2))
    rec("只签 memory 时 handoff 仍拒", "拒绝" in r.get(2, ""))

    # 4 签 handoff → 成功 + allowed
    grant("sign", "--tools", "write:handoff", "--hours", "2", "--reason", "体检")
    r = rpc(hb(3))
    rec("签 handoff 后可写", "已写入" in r.get(3, ""))
    rec("allowed 留痕", '"event": "allowed"' in glog.read_text(encoding="utf-8"))

    # 5 撤销 → 立即拒
    gj = json.loads((SB / "入口" / "_签发.json").read_text(encoding="utf-8"))
    gid = [x for x in gj["grants"] if "write:handoff" in x["tools"]][-1]["id"]
    rc, o = grant("revoke", "--id", gid)
    r = rpc(hb(4))
    rec("撤销后立即拒", rc == 0 and "拒绝" in r.get(4, ""), o[:80])

    # 6 超限退 A
    outs = ""
    for i in range(3):
        _, o = grant("sign", "--tools", "write:handoff", "--hours", "4", "--reason", f"体检{i}")
        outs += o
    rec("24h 超限退 A", "退到 A 模式" in outs)
    rec("超限后仍能签", "已签发" in outs)

    # 7 preflight 三模式
    for mode in ("日常", "工作", "完整"):
        rc, out, err = run("preflight.py", "--mode", mode, "--no-heartbeat")
        rec(f"preflight {mode}", rc in (0, 1), (out + err)[:80])

    # 8 recall 三态
    r = rpc({"jsonrpc": "2.0", "id": "r1", "method": "tools/call",
             "params": {"name": "recall", "arguments": {"question": "还记得壳那件事吗", "keywords": "壳"}}},
            {"jsonrpc": "2.0", "id": "r2", "method": "tools/call",
             "params": {"name": "recall", "arguments": {"question": "上次那个"}}},
            {"jsonrpc": "2.0", "id": "r3", "method": "tools/call",
             "params": {"name": "recall", "arguments": {"question": "你觉得这事该不该做"}}})
    rec("recall 命中", "片段" in r.get("r1", ""), r.get("r1", "")[:60])
    rec("recall 线索太少", "需要更多线索" in r.get("r2", ""))
    rec("recall 观点类不查", "不用查" in r.get("r3", ""))

    # 9 装配器
    rc, out, _ = run("pack_assembler.py", "check", "--task", "写稿")
    rec("装配：写稿缺素材拒（E06）", rc == 2 and "E06" in out, out[:80])
    rc, out, _ = run("pack_assembler.py", "check", "--task", "直播")
    rec("装配：直播通过", rc == 0, out[:80])
    rc, out, _ = run("pack_assembler.py", "build", "--task", "写稿", "--material", "本期素材")
    rec("装配：写稿落地并对账", rc == 0 and "对账：OK" in out, out[-120:])
    mf = SB / "启动包" / "装配版" / "写稿.manifest.json"
    man = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    rec("manifest 三态齐", all(k in man for k in ("requested", "loaded", "injected", "injected_hash")))

    # 10 任务看板/预算/轮转/校验
    rc, out, _ = run("workbench.py", "prune")
    rec("prune 出候选", rc == 1 and "已达：" in out, out[:80])
    rc, out, _ = run("budget.py")
    rec("预算在目标内", "全部在目标内" in out, out[-80:])
    rc, out, _ = run("handoff.py", "rotate", "--dry-run")
    rec("rotate dry-run", rc == 0 and "dry-run" in out, out[:80])
    rc, out, _ = run("l3.py", "check")
    rec("L3 一致", rc == 0, out[-120:])

    # 11 MCP（副本里起，临时端口）
    port = 8899
    proc = subprocess.Popen([PY, str(SB / "入口" / "her-mcp-http.py"), "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env_of())
    try:
        import urllib.request
        time.sleep(2.0)
        tok = (SB / "入口" / "mcp-token.txt").read_text(encoding="utf-8").strip()

        def post(payload, token=tok, accept="application/json"):
            data = json.dumps(payload, ensure_ascii=False).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp?token={token}", data=data,
                                         headers={"Content-Type": "application/json", "Accept": accept})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read().decode()

        st, body = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        rec("MCP initialize", st == 200 and "protocolVersion" in body)
        st, body = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        rec("MCP 暴露 4 个只读工具", st == 200 and body.count('"name"') == 4, body[:60])
        st, body_sse = post({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, accept="text/event-stream")
        rec("MCP SSE 帧", body_sse.startswith("event: message"), body_sse[:40])
        try:
            post({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, token="bad")
            rec("MCP 错 token 被拒", False)
        except Exception as e:
            rec("MCP 错 token 被拒", "403" in str(e), str(e)[:60])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    # 12.5 GBK 控制台安全（她踩过：check-num 打对勾符号直接崩）
    bad_chars = {}
    for _d in ("脚本", "入口"):
        for _p in (REAL / _d).glob("*.py"):
            if _p.name in ("healthcheck.py", "selftest.py"):
                for _line in _p.read_text(encoding="utf-8", errors="replace").splitlines():
                    for _ch in _line:
                        try:
                            _ch.encode("gbk")
                        except UnicodeEncodeError:
                            bad_chars.setdefault(_p.name, set()).add(_ch)
            else:
                for _line in _p.read_text(encoding="utf-8", errors="replace").splitlines():
                    for _ch in _line:
                        try:
                            _ch.encode("gbk")
                        except UnicodeEncodeError:
                            bad_chars.setdefault(_p.name, set()).add(_ch)
    rec("脚本输出字符 GBK 安全", not bad_chars, dict(list(bad_chars.items())[:5]))

    # 12 沙箱自检
    q = subprocess.run([PY, str(REAL / "脚本" / "selftest.py")], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900)
    rc, out, err = q.returncode, (q.stdout or ""), (q.stderr or "")
    m = re.search(r"通过\s*(\d+)/(\d+)", out or "")
    rec(f"selftest {m.group(0) if m else '未跑'}", rc == 0 and m and m.group(1) == m.group(2), out[-200:])

    # 13 真实引擎只读证明
    after = fingerprint()
    rec("真实引擎未被改动", before == after, f"{before} → {after}")

    passed = len(ok)
    print(f"\n合计 {passed + len(bad)} 项：通过 {passed}｜失败 {len(bad)}")
    if bad:
        print("\n失败清单：")
        for n, e in bad:
            print(f"  - {n}：{e}")
    if not a.keep:
        force_rmtree(SB)
        print("沙箱已删")
    else:
        print(f"沙箱保留：{SB}")
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    main()
