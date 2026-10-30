#!/usr/bin/env python3
"""spec_apply —— 人格/ 目录的提案制入口（提案 → 签字 → 自动落盘）。

背景：`人格/` 是 ACL 只读，普通进程改不动；每次手工复制很麻烦。
所以：普通人只写"提案"，**签字落盘那一步需要管理员**（本脚本 accept 会检查）。

用法：
  python 脚本/spec_apply.py propose --src 规格/L1-规范-20261029-新全文.md \
        --target 人格/L1-规范.md --why "按 2026-10-29 决定：证据三类/准入三条/40 天过期/合并"
  python 脚本/spec_apply.py list
  python 脚本/spec_apply.py accept --id P-0001        # 需要管理员
  python 脚本/spec_apply.py reject --id P-0001 --why "…"
  python 脚本/spec_apply.py show --id P-0001

退出码：0 成功 / 2 参数或文件问题 / 3 权限不足 / 4 校验不符
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ["HER_HOME"]).resolve() if _os.environ.get("HER_HOME") else ROOT
PROPS = DATA / "提案"
LOG = DATA / "记忆" / "L3" / "_提案.jsonl"
BACKUP = DATA.parent / "备份" if (DATA.parent / "备份").exists() else DATA / "备份"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def sha(p):
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def load():
    if not PROPS.exists():
        return {"props": []}
    f = PROPS / "_提案.json"
    if not f.exists():
        return {"props": []}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"props": []}


def save(d):
    PROPS.mkdir(parents=True, exist_ok=True)
    (PROPS / "_提案.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def log(rec):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), **rec}, ensure_ascii=False) + "\n")


def cmd_propose(a):
    src = DATA / a.src
    target = DATA / a.target
    if not src.exists():
        print(f"提案文件不存在：{a.src}")
        return 2
    d = load()
    n = len(d["props"]) + 1
    pid = f"P-{n:04d}"
    prop = {
        "id": pid, "created": now(), "why": a.why or "",
        "src": a.src, "target": a.target,
        "src_sha": sha(src), "target_sha_now": sha(target) if target.exists() else "",
        "target_han_now": (sum(1 for c in target.read_text(encoding="utf-8")
                               if "\u4e00" <= c <= "\u9fff") if target.exists() else 0),
        "src_han": sum(1 for c in src.read_text(encoding="utf-8") if "\u4e00" <= c <= "\u9fff"),
        "state": "pending", "by": a.by,
    }
    d["props"].append(prop)
    save(d)
    log({"event": "propose", **prop})
    print(f"[提案已建] {pid}  {a.src} → {a.target}")
    print(f"           {prop['target_han_now']} 汉字 → {prop['src_han']} 汉字｜理由：{a.why or '（无）'}")
    print(f"           签字落盘要管理员：python 脚本\\spec_apply.py accept --id {pid}")
    return 0


def cmd_list(a):
    d = load()
    if not d["props"]:
        print("（没有提案）")
        return 0
    for p in d["props"]:
        mark = {"pending": "待签", "accepted": "已落盘", "rejected": "已驳回"}.get(p["state"], p["state"])
        print(f"{p['id']}  {mark:<5}  {p['created']}  {p['src']} → {p['target']}")
    return 0


def cmd_show(a):
    d = load()
    hit = [p for p in d["props"] if p["id"] == a.id]
    if not hit:
        print(f"没有 {a.id}")
        return 2
    print(json.dumps(hit[0], ensure_ascii=False, indent=2))
    return 0


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def cmd_accept(a):
    d = load()
    hit = [p for p in d["props"] if p["id"] == a.id and p["state"] == "pending"]
    if not hit:
        print(f"没有待签的提案 {a.id}")
        return 2
    p = hit[0]
    src = DATA / p["src"]
    target = DATA / p["target"]
    if not src.exists():
        print(f"提案正文不见了：{p['src']}")
        return 2
    if sha(src) != p["src_sha"]:
        print("提案正文被改过（sha 不符），重新提一次")
        return 4
    if not is_admin():
        print("需要管理员：这一步会写 人格/。请用 入口\\apply-proposal.cmd，"
              "或在管理员 PowerShell 里重跑这条命令")
        return 3
    BACKUP.mkdir(parents=True, exist_ok=True)
    if target.exists():
        bk = BACKUP / f"{target.name}.改前-{datetime.now().strftime('%Y%m%d-%H%M')}.bak"
        shutil.copy2(target, bk)
        print(f"[备份] {bk.name}")
    shutil.copy2(src, target)
    p["state"] = "accepted"
    p["accepted_at"] = now()
    p["accepted_by"] = a.by
    save(d)
    log({"event": "accept", "id": p["id"], "target": p["target"], "by": a.by})
    print(f"[已落盘] {p['target']}（{p['src_han']} 汉字）")
    _verify(p["target"])
    return 0


def _verify(target_rel):
    """落盘后自检：按目标类型选最小验证，不让人记该跑哪条。"""
    import subprocess as _sp
    py = sys.executable
    target = DATA / target_rel
    checks = []
    if target.name.startswith("人格卡"):
        checks.append(("L0 指纹", [_py(), str(DATA / "脚本" / "l0_guard.py")]))
    checks.append(("preflight", [_py(), str(DATA / "脚本" / "preflight.py"),
                                 "--mode", "日常", "--no-heartbeat"]))
    for name, cmd in checks:
        try:
            r = _sp.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            tail = [l for l in (r.stdout or "").splitlines() if l.strip()]
            last = tail[-1][:80] if tail else ""
            ok = r.returncode in (0, 1)
            print(f"  [{'OK' if ok else 'X'}] {name}：rc={r.returncode}｜{last}")
        except Exception as e:
            print(f"  [X] {name} 跑不起来：{e}")


def _py():
    return sys.executable


def cmd_reject(a):
    d = load()
    hit = [p for p in d["props"] if p["id"] == a.id and p["state"] == "pending"]
    if not hit:
        print(f"没有待签的提案 {a.id}")
        return 2
    hit[0]["state"] = "rejected"
    hit[0]["rejected_why"] = a.why or ""
    save(d)
    log({"event": "reject", "id": a.id, "why": a.why or ""})
    print(f"[已驳回] {a.id}")
    return 0


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="人格/ 提案制入口")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--src", required=True); p.add_argument("--target", required=True)
    p.add_argument("--why", default=""); p.add_argument("--by", default="{维护者}")
    p.set_defaults(fn=cmd_propose)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("show"); p.add_argument("--id", required=True); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("accept"); p.add_argument("--id", required=True)
    p.add_argument("--by", default="maker"); p.set_defaults(fn=cmd_accept)
    p = sub.add_parser("reject"); p.add_argument("--id", required=True)
    p.add_argument("--why", default=""); p.set_defaults(fn=cmd_reject)
    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
