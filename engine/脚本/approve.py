#!/usr/bin/env python3
"""approve —— 「待批准」队列：她申请，maker 一键同意。

为什么要有：她提审批的时候（要写、要卡、要改规范），maker 不用记"该签什么"，
点一下等于签对应的票。

用法：
  python 脚本/approve.py list            # 看等着批的
  python 脚本/approve.py ok <id>         # 同意（自动签对应的票）
  python 脚本/approve.py no <id> --why "…"
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ["HER_HOME"]).resolve() if os.environ.get("HER_HOME") else ROOT
Q = DATA / "入口" / "_批准.json"
LOG = DATA / "记忆" / "L3" / "_批准.jsonl"
GRANT = ROOT / "脚本" / "grant.py"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def load():
    if not Q.exists():
        return {"items": []}
    try:
        return json.loads(Q.read_text(encoding="utf-8"))
    except Exception:
        return {"items": []}


def save(d):
    Q.parent.mkdir(parents=True, exist_ok=True)
    Q.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def log(rec):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), **rec}, ensure_ascii=False) + "\n")


def add(tool, need, why, scene):
    """宿主在"因为没票被拒"时调用。同 tool+why 10 分钟内不重复记。"""
    d = load()
    for it in d["items"]:
        if it.get("tool") == tool and it.get("why") == why and it.get("state") == "pending":
            return it["id"]
    n = len(d["items"]) + 1
    it = {"id": f"A-{n:04d}", "tool": tool, "need": need, "why": why,
          "scene": scene, "asked_at": now(), "state": "pending"}
    d["items"].append(it)
    save(d)
    log({"event": "asked", **it})
    return it["id"]


def cmd_list(_):
    d = load()
    pend = [x for x in d["items"] if x.get("state") == "pending"]
    if not pend:
        print("（没有待批准）")
        return
    print(f"待批准 {len(pend)} 条：")
    for x in pend:
        print(f"  {x['id']}  {x['asked_at']}  {x['tool']}（要 {x['need']}）  {x['why'][:40]}")


def cmd_ok(a):
    d = load()
    hit = [x for x in d["items"] if x["id"] == a.id and x.get("state") == "pending"]
    if not hit:
        print(f"没有待批准的 {a.id}")
        return 2
    it = hit[0]
    need = it.get("need") or ""
    if need:
        cmd = [sys.executable, str(GRANT), "sign", "--tools", need,
               "--by-request", "--reason", f"批准 {it['id']}：{it['why'][:30]}"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"[X] 签票失败：{(r.stderr or r.stdout)[:160]}")
            return 3
        print((r.stdout or "").strip().splitlines()[0])
    it["state"] = "approved"
    it["approved_at"] = now()
    save(d)
    log({"event": "approved", "id": it["id"], "need": need})
    print(f"[已批准] {it['id']}（{it['tool']}）")
    return 0


def cmd_no(a):
    d = load()
    hit = [x for x in d["items"] if x["id"] == a.id and x.get("state") == "pending"]
    if not hit:
        print(f"没有待批准的 {a.id}")
        return 2
    hit[0]["state"] = "rejected"
    hit[0]["rejected_why"] = a.why or ""
    save(d)
    log({"event": "rejected", "id": a.id, "why": a.why or ""})
    print(f"[已驳回] {a.id}")
    return 0


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="待批准队列")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("ok"); p.add_argument("id"); p.set_defaults(fn=cmd_ok)
    p = sub.add_parser("no"); p.add_argument("id"); p.add_argument("--why", default=""); p.set_defaults(fn=cmd_no)
    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
