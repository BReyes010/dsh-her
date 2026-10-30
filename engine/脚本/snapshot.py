#!/usr/bin/env python3
"""snapshot —— 冻结只读快照（回退的前置条件）。

没有冻结就没有回退：三个包每次收尾都重写，不冻住的话"退回去"退到的是明天那个包。

用法：
  python 脚本/snapshot.py freeze --note "切卡方案前"
  python 脚本/snapshot.py list
  python 脚本/snapshot.py verify [--name 20261029-0115]
  python 脚本/snapshot.py restore --name 20261029-0115 --to <目录>   # 只往目录里放，不覆盖现役
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ["HER_HOME"]).resolve() if _os.environ.get("HER_HOME") else ROOT
HIST = DATA / "启动包" / "历史"

TARGETS = [
    ("启动包", "*.txt"),
    ("启动包/外部客户端版", "*.txt"),
    ("skills", "*.md"),
    ("skills", "_tasks.json"),
    ("入口", "任务看板.json"),
    ("入口", "任务看板.md"),
    ("人格", "人格卡.md"),
]


def sha(p):
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def collect():
    files = []
    for rel, pat in TARGETS:
        d = DATA / rel
        if not d.exists():
            continue
        for p in sorted(d.glob(pat)):
            if p.is_file():
                files.append(p)
    return files


def cmd_freeze(a):
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = HIST / stamp
    n = 2
    while out.exists():
        out = HIST / f"{stamp}-{n}"
        n += 1
    out.mkdir(parents=True)
    meta = {"frozen_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "note": a.note or "", "files": {}, "count": 0}
    for p in collect():
        rel = p.relative_to(DATA).as_posix()
        dst = out / rel.replace("/", "__")
        shutil.copy2(p, dst)
        meta["files"][rel] = {"sha": sha(p), "bytes": p.stat().st_size, "copy": dst.name}
    meta["count"] = len(meta["files"])
    (out / "_snapshot.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    # 设只读
    for f in out.iterdir():
        try:
            os.chmod(f, stat.S_IREAD)
        except Exception:
            pass
    print(f"[已冻结] {out.relative_to(DATA)}｜{meta['count']} 个文件｜{meta['frozen_at']}")
    if meta["note"]:
        print(f"         备注：{meta['note']}")
    return 0


def cmd_list(a):
    if not HIST.exists():
        print("还没有任何快照。切换方案前请先 freeze。")
        return 0
    for d in sorted(HIST.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        m = d / "_snapshot.json"
        if not m.exists():
            continue
        meta = json.loads(m.read_text(encoding="utf-8"))
        print(f"  {d.name}  {meta['count']:>2} 个文件  {meta.get('note', '')}")
    return 0


def cmd_verify(a):
    if not HIST.exists():
        print("[FAIL] 没有历史目录")
        return 2
    dirs = [d for d in sorted(HIST.iterdir(), reverse=True) if (d / "_snapshot.json").exists()]
    if a.name:
        dirs = [d for d in dirs if d.name == a.name]
    if not dirs:
        print("[FAIL] 没有可校验的快照")
        return 2
    d = dirs[0]
    meta = json.loads((d / "_snapshot.json").read_text(encoding="utf-8"))
    bad = 0
    for rel, info in meta["files"].items():
        src = DATA / rel
        if not src.exists():
            print(f"  [缺] {rel}")
            bad += 1
        elif sha(src) != info["sha"]:
            print(f"  [变] {rel}（现役已改，快照仍是旧的）")
    print(f"[{'OK' if not bad else 'WARN'}] {d.name}：{meta['count']} 个文件，现役缺失 {bad} 个")
    return 0 if not bad else 1


def cmd_restore(a):
    d = HIST / a.name
    if not (d / "_snapshot.json").exists():
        print(f"[FAIL] 没有快照 {a.name}")
        return 2
    to = Path(a.to)
    to.mkdir(parents=True, exist_ok=True)
    meta = json.loads((d / "_snapshot.json").read_text(encoding="utf-8"))
    n = 0
    for rel, info in meta["files"].items():
        src = d / info["copy"]
        if not src.exists():
            continue
        dst = to / rel.replace("/", "__")
        shutil.copy2(src, dst)
        try:
            os.chmod(dst, stat.S_IWRITE)
        except Exception:
            pass
        n += 1
    print(f"[已放出] {n} 个文件 → {to}（不覆盖现役，确认后再手动替换）")
    return 0


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="冻结只读快照")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("freeze")
    p.add_argument("--note", default="")
    sub.add_parser("list")
    p = sub.add_parser("verify")
    p.add_argument("--name", default="")
    p = sub.add_parser("restore")
    p.add_argument("--name", required=True)
    p.add_argument("--to", required=True)
    a = ap.parse_args()
    return {"freeze": cmd_freeze, "list": cmd_list, "verify": cmd_verify, "restore": cmd_restore}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
