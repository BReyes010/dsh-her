#!/usr/bin/env python3
"""paths：全仓唯一的路径解析入口。

默认卡来自 cards/_registry.json 的 default_card；可用环境变量 HER_CARD 覆盖。
现役卡（live）走相对路径指向 `../人格`、`../记忆`——她的文件原地不动。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 用户数据根：设了 HER_HOME 就用它（代码留在 ROOT，升级只换代码）
DATA = Path(os.environ["HER_HOME"]).resolve() if os.environ.get("HER_HOME") else ROOT
sys.path.insert(0, str(ROOT / "脚本"))


def rel(p):
    """打印用：能相对 DATA 就相对 DATA，否则相对 ROOT，都不行就原样。"""
    p = Path(p)
    for base in (DATA, ROOT):
        try:
            return p.relative_to(base)
        except ValueError:
            continue
    return p


def _kit():
    import kit as k
    return k


def default_card():
    k = _kit()
    reg = k.load_json(DATA / "cards" / "_registry.json", {})
    return os.environ.get("HER_CARD") or reg.get("default_card") or "dafeiyu"


def paths(card_id=None):
    k = _kit()
    cid = card_id or default_card()
    r = k.resolve(cid)
    scenes = {s["id"]: [str(f.resolve()) for f in s["files"]] for s in r["scenes"]}
    kp = r["kit_paths"] or {}
    l3 = Path(kp["l3"]).resolve() if kp.get("l3") else None
    views = [Path(v).resolve() for v in (kp.get("views") or [])]
    return {
        "card_id": cid,
        "card": r["card"],
        "scenes": scenes,
        "persona": Path(scenes["日常"][0]) if "日常" in scenes else None,
        "db": Path(kp["db"]).resolve() if kp.get("db") else None,
        "l1": Path(kp["l1"]).resolve() if kp.get("l1") else None,
        "l3": l3,
        "handoff": (l3 / "handoff") if l3 else None,
        "views": views,
        "memory_md": views[0] if views else None,
        "scene_md": views[1] if len(views) > 1 else None,
        "desk": DATA / "入口" / "任务看板.md",
        "startup": DATA / "启动包" / "日常模式.txt",
        "memory_id": r["memory_id"],
    }


if __name__ == "__main__":
    for s in (sys.stdout,):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    p = paths()
    print(f"卡：{p['card_id']}（记忆套件 {p['memory_id']}）")
    for k, v in p.items():
        if k in ("card", "scenes"):
            continue
        print(f"  {k:<10} {v}")
    print("  场景：", {k: len(v) for k, v in p["scenes"].items()})
