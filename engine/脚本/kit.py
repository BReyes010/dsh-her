#!/usr/bin/env python3
"""kit：人格卡 × 记忆套件 解析层（v2）。

两种来源：
  live    —— 注册表里的相对路径指向真实文件（现役角色原地不动）
  template/local —— 卡自带目录 + card.json

id 规则（写死）：
  · 卡 id 与套件 id 是两个命名空间，各自唯一、永不复用
  · 默认配对 = card.memory_id
  · 允许覆盖：resolve(card_id, memory_id=...)，覆盖方必须自己留痕
  · 禁止：live 卡导出；template/local 卡指向 live 套件

用法：
  python kit.py --list
  python kit.py --card dafeiyu [--memory dafeiyu-template]
  python kit.py --check
"""

import argparse
import json
import sys
from pathlib import Path

from paths import rel

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
CARDS = DATA / "cards"
MEM = DATA / "memory"


def load_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def cards():
    reg = load_json(CARDS / "_registry.json", {"cards": []})
    out = []
    for c in reg.get("cards", []):
        meta = {}
        if c.get("kind") == "template" or c.get("dir"):
            meta = load_json(CARDS / c["dir"] / "card.json", {})
        out.append({**meta, **c})
    return out


def kits():
    reg = load_json(MEM / "_registry.json", {"kits": []})
    return reg.get("kits", [])


def get_card(cid):
    return next((c for c in cards() if c.get("id") == cid), None)


def get_kit(kid):
    return next((k for k in kits() if k.get("id") == kid), None)


def _card_base(card):
    """live 卡的相对路径以 cards/ 为基准；模板卡以自己目录为基准。"""
    if card.get("kind") == "live":
        return CARDS
    return CARDS / card["dir"]


def _kit_paths(kit):
    if kit.get("kind") == "live":
        return {
            "db": MEM / kit["db"],
            "l1": MEM / kit.get("l1", ""),
            "l3": MEM / kit["l3"],
            "views": [MEM / v for v in kit.get("views", [])],
            "dir": None,
        }
    d = MEM / kit["dir"]
    return {"db": d / "memory.db", "l1": d / "l1.db", "l3": d / "L3", "views": [d / "记忆.md"], "dir": d}


def resolve(card_id, memory_id=None):
    c = get_card(card_id)
    if not c:
        raise SystemExit(f"没有这张卡：{card_id}")
    base = _card_base(c)
    scenes = []
    for s in c.get("scenes", []):
        files = [base / f for f in s.get("files", [])]
        scenes.append({
            "id": s["id"], "label": s.get("label", s["id"]), "files": files,
            "missing": [rel(str(f)) for f in files if not f.exists()],
            "memory_scene": s.get("memory_scene", "通用"),
        })
    default_mid = c.get("memory_id")
    use_mid = memory_id or default_mid
    kit = get_kit(use_mid)
    # 规则：非 live 卡不得指向 live 套件
    if kit and c.get("kind") != "live" and kit.get("kind") == "live":
        raise SystemExit(f"禁止：卡 {card_id}（{c.get('kind')}）不得指向 live 套件 {use_mid}")
    return {
        "card": c, "card_id": card_id, "scenes": scenes,
        "memory_id": use_mid, "memory_overridden": bool(memory_id and memory_id != default_mid),
        "kit": kit, "kit_paths": _kit_paths(kit) if kit else None,
    }


def assert_exportable(card_id):
    c = get_card(card_id)
    if not c:
        raise SystemExit(f"没有这张卡：{card_id}")
    if c.get("kind") == "live":
        raise SystemExit(f"禁止导出：{card_id} 是现役卡（live）。要导出请用 template 卡。")


def check():
    ok = True
    ids = [c.get("id") for c in cards()]
    kids = [k.get("id") for k in kits()]
    if len(set(ids)) != len(ids):
        ok = False
        print("[X] 卡 id 有重复")
    if len(set(kids)) != len(kids):
        ok = False
        print("[X] 套件 id 有重复")
    for c in cards():
        try:
            r = resolve(c["id"])
        except SystemExit as e:
            ok = False
            print("[X]", e)
            continue
        for s in r["scenes"]:
            if s["missing"]:
                ok = False
                print(f"[X] {c['id']} / 场景「{s['id']}」缺文件：{s['missing']}")
        if not r["kit"]:
            ok = False
            print(f"[X] {c['id']} 的默认套件不存在：{c.get('memory_id')}")
        elif r["kit_paths"] and not r["kit_paths"]["db"].exists():
            ok = False
            print(f"[X] {c['id']} 的套件缺 memory.db：{r['kit_paths']['db']}")
    print("[OK] 校验通过" if ok else "[X] 有问题（见上）")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="人格卡 / 记忆套件解析层")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--card")
    ap.add_argument("--memory")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    if a.check:
        sys.exit(check())
    if a.card:
        r = resolve(a.card, a.memory)
        c = r["card"]
        print(f"卡：{c.get('name')}（{c.get('id')} · {c.get('kind')} · {c.get('version','')}）")
        print(f"记忆套件：{r['memory_id']}（默认 {c.get('memory_id')}）"
              + ("  ← 已被显式覆盖，记得留痕" if r["memory_overridden"] else ""))
        if r["kit_paths"]:
            print(f"  db: {rel(r['kit_paths']['db'])}")
            print(f"  l3: {rel(r['kit_paths']['l3'])}")
        for s in r["scenes"]:
            print(f"  场景「{s['id']}」（{s['label']}）→ 记忆场景 {s['memory_scene']}")
            for f in s["files"]:
                print(f"    {'[OK]' if f.exists() else '[X]'} {rel(f)}")
        return

    print(f"{'id':<20}{'kind':<10}{'名字':<22}{'默认套件':<18}场景")
    print("-" * 92)
    for c in cards():
        sc = "、".join(s.get("id", "") for s in c.get("scenes", []))
        print(f"{c.get('id',''):<20}{c.get('kind',''):<10}{c.get('name',''):<22}{c.get('memory_id',''):<18}{sc}")
    print(f"\n记忆套件：{', '.join(k['id'] for k in kits())}")


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    main()
