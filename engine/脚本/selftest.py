#!/usr/bin/env python3
"""selftest：引擎全量沙箱测试。**只在副本里跑，绝不碰真数据。**

做法：把整个 `人格与记忆/` 复制到 `.tmp-selftest/`，在副本里跑一串断言，
跑完删副本。因为脚本都用 `Path(__file__).parent.parent` 定根，副本天然隔离。

用法：
  python 脚本/selftest.py            # 跑全部
  python 脚本/selftest.py --keep     # 保留副本（排查用）
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
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REAL = Path(__file__).resolve().parent.parent          # 代码根
DATA_ROOT = Path(os.environ["HER_HOME"]).resolve() if os.environ.get("HER_HOME") else REAL
SANDBOX = DATA_ROOT.parent / ".tmp-selftest"           # 复制「数据根」，代码不复制


def run(root, *args, cwd=None, timeout=180):
    p = subprocess.run([sys.executable, str(root / "脚本" / args[0]), *args[1:]],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(cwd or root), timeout=timeout)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    if SANDBOX.exists():
        force_rmtree(SANDBOX)
    print(f"沙箱：{SANDBOX}")
    shutil.copytree(DATA_ROOT, SANDBOX, ignore=shutil.ignore_patterns(
        "备份", "产出", "素材", "出差包", ".tmp*", "__pycache__", "发布", "历史"))

    S = SANDBOX

    # 沙箱必须没有真实签发，否则「无签发被拒」用例会假通过
    for _p in (S / "入口" / "_签发.json", S / "记忆" / "L3" / "_签发.jsonl"):
        if _p.exists():
            _p.unlink()

    ok = []

    def check(name, cond, extra=""):
        ok.append((name, bool(cond)))
        print(f"[{'OK' if cond else 'FAIL'}] {name}" + ("" if cond else f" — {extra[:120]}"))

    # 1 自检
    rc, out, err = run(S, "kit.py", "--check")
    check("kit --check", rc == 0, out + err)

    # 2 L0 守卫
    rc, out, err = run(S, "l0_guard.py")
    check("l0_guard 指纹一致", rc == 0, out + err)

    # 3 L3 校验
    rc, out, err = run(S, "l3.py", "check")
    check("l3 check 干净", rc == 0, out + err)

    # 4 记忆：导出 + 编号校验
    rc, out, err = run(S, "memory_db.py", "check-num")
    check("memory_db check-num", rc == 0, out + err)
    rc, out, err = run(S, "memory_db.py", "export")
    check("memory_db export", rc == 0 and (S / "记忆" / "记忆.md").exists(), out + err)

    # 5 handoff：写 + 状态
    body = S / ".tmp-handoff.md"
    body.write_text("## 条目 1\n谁做的：她\n做了什么：沙箱测试条目\n主题：沙箱\n", encoding="utf-8")
    rc, out, err = run(S, "handoff.py", "write", "--client", "沙箱", "--file", str(body))
    check("handoff write", rc == 0, out + err)
    rc, out, err = run(S, "handoff.py", "status")
    check("handoff status", rc == 0, out + err)

    # 6 笔记：写触动 → 立刻能检索（写入即索引）
    rc, out, err = run(S, "notes.py", "add", "--kind", "触动", "--text", "沙箱里的一句话",
                       "--source", "沙箱", "--why", "测链路")
    queued = "PENDING" in out
    check("notes add（或进待审）", rc == 0 and ("已写入正文" in out or "触动 +1" in out or queued), out + err)
    if queued:
        import re as _re
        m = _re.search(r"#(\d+)", out)
        if m:
            run(S, "notes.py", "approve", "--id", m.group(1))
    rc, out, err = run(S, "l3.py", "grep", "沙箱里的一句话", "--kind", "触动")
    check("写完立刻可检索", "共 1 处" in out, out + err)

    # 6.5 日常原话：写一句 → 能在包里出现
    rc, out, err = run(S, "daily_quotes.py", "add", "--who", "他", "--text", "沙箱里的一句话" if False else "沙箱日常一句")
    check("daily_quotes add", rc == 0, out + err)
    rc, out, err = run(S, "daily_quotes.py", "stat")
    check("daily_quotes stat", "日常原话：" in out, out + err)

    # 7 查询代理
    rc, out, err = run(S, "l3q.py", "recall", "--question", "还记得沙箱里的一句话吗", "--keywords", "沙箱")
    check("l3q recall 有输出", "片段" in out or "没找到" in out or "线索" in out, out + err)

    # 8 归档 sha 一致
    rc, out, err = run(S, "wb_archive.py")
    meta = json.loads((S / "记忆" / "L3" / "归档" / "_归档.json").read_text(encoding="utf-8"))
    import hashlib
    arc = (S / "记忆" / "L3" / "归档" / "任务看板-已作废.md").read_bytes()
    check("归档 sha 与字节一致", meta.get("sha256") == hashlib.sha256(arc).hexdigest(), "")

    # 9 渲染 + 出包 + meta
    rc, out, err = run(S, "workbench.py", "render")
    check("render 成功", rc == 0 and (S / "启动包" / "_meta.json").exists(), out + err)
    meta2 = json.loads((S / "启动包" / "_meta.json").read_text(encoding="utf-8"))
    packs2 = meta2.get("packs", {})
    check("出差包 + 两个预设都出了",
          len(packs2) == 3 and "出差包.txt" in packs2
          and "预设-写稿.txt" in packs2 and "预设-直播.txt" in packs2, str(list(packs2)))
    _core = (S / "启动包" / "出差包.txt")
    check("出差包带技能索引", _core.exists() and "技能索引" in _core.read_text(encoding="utf-8"))

    # 10 preflight 三模式（0/1 都算通过）
    for mode in ("日常", "工作", "完整"):
        rc, out, err = run(S, "preflight.py", "--mode", mode)
        check(f"preflight {mode} rc∈{{0,1}}", rc in (0, 1), out + err)

    # 11 回程 intake（dry-run）
    sample = S / ".tmp-return.md"
    sample.write_text("""主题：沙箱回程
谁做的：一起
做了什么：测回程
起点：无
卡点：无
走过的岔路：无
试过没成的：无
取舍：无
哪里还没想明白：无
原话：第一句。
原话：第二句。
原话：第三句。
open：无
closes：无
留下：无
""", encoding="utf-8")
    rc, out, err = run(S, "returnflow.py", "intake", "--file", str(sample), "--dry-run")
    check("returnflow dry-run", rc == 0 and "[dry-run]" in out, out + err)

    # 11.5 写权闸：判据是签发（不是模式/机器）
    def _rpc(seq, env_extra=None):
        import json as _j
        import os as _os
        inp = chr(10).join(_j.dumps(x, ensure_ascii=False) for x in seq) + chr(10)
        env2 = dict(_os.environ)
        env2["HER_HOME"] = str(S)
        if env_extra:
            env2.update(env_extra)
        q = subprocess.run([sys.executable, str(S / "入口" / "her-host.py")], input=inp,
                           capture_output=True, text=True, encoding="utf-8", env=env2)
        out = {}
        for line in (q.stdout or "").splitlines():
            if line.strip().startswith("{"):
                try:
                    d = _j.loads(line)
                    out[d["id"]] = d["result"]["content"][0]["text"]
                except Exception:
                    pass
        return out

    def _grant(*a):
        import os as _os
        env2 = dict(_os.environ, HER_HOME=str(S))
        q = subprocess.run([sys.executable, str(S / "脚本" / "grant.py"), *a],
                           capture_output=True, text=True, encoding="utf-8", env=env2)
        return q.returncode, (q.stdout or "") + (q.stderr or "")

    def _hb(i, client="自检"):
        return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                "params": {"name": "write_handoff",
                           "arguments": {"client": client,
                                         "content": f"# handoff · 2026-10-29 01:00 · 客户端：{client}\n\n## 条目 1\n谁做的：她\n做了什么：自检\n主题：自检\nopen：false\n"}}}

    r = _rpc([_hb(1)])
    check("无签发写 handoff 被拒", "拒绝" in r.get(1, ""), r.get(1, "")[:80])
    glog = S / "记忆" / "L3" / "_签发.jsonl"
    gtxt = glog.read_text(encoding="utf-8") if glog.exists() else ""
    check("越权尝试系统侧留痕", '"event": "attempt"' in gtxt and '"event": "denied"' in gtxt, gtxt[-160:])

    rc, o = _grant("sign", "--tools", "write:handoff", "--hours", "2", "--reason", "自检")
    r = _rpc([_hb(2)])
    check("签发后写成功", "已写入" in r.get(2, ""), r.get(2, "")[:80])
    gtxt = glog.read_text(encoding="utf-8")
    check("成功也留痕", '"event": "allowed"' in gtxt, "")

    r = _rpc([{"jsonrpc": "2.0", "id": "g", "method": "tools/call", "params": {"name": "get_persona", "arguments": {"scene": "日常"}}},
              {"jsonrpc": "2.0", "id": "a", "method": "tools/call", "params": {"name": "wb_retire", "arguments": {"id": "D-32", "why": "沙箱测"}}}])
    # 2026-10-29：任务看板退役改为「免票但必须写理由」
    check("免票 retire 带理由即放行", "拒绝" not in r.get("a", ""), r.get("a", "")[:80])
    r0 = _rpc([{"jsonrpc": "2.0", "id": "z", "method": "tools/call",
                "params": {"name": "wb_retire", "arguments": {"id": "D-33"}}}])
    check("retire 不写理由仍被拒", "理由" in r0.get("z", ""), r0.get("z", "")[:80])

    rc, o = _grant("sign", "--tools", "write:state", "--reason", "再签")
    check("state 只走 A 模式", "A 模式" in o, o[:120])

    # 12 预算
    rc, out, err = run(S, "budget.py")
    check("budget 可算", "总判定" in out, out + err)

    passed = sum(1 for _, b in ok if b)
    print(f"\n通过 {passed}/{len(ok)}")
    if not a.keep:
        force_rmtree(SANDBOX)
        print("沙箱已删")
    else:
        print("沙箱保留（--keep）")
    sys.exit(0 if passed == len(ok) else 1)


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    main()
