#!/usr/bin/env python3
"""her_skill —— 技能卡包：导出 / 检查 / 导入。

一张技能卡带它的子卡（kind: task，skill: 父卡）一起走。

用法：
  python 脚本/her_skill.py export 口播 --out 口播.zip
  python 脚本/her_skill.py inspect 口播.zip
  python 脚本/her_skill.py import 口播.zip [--force]

规范（导入会检查）：
  frontmatter 必有：kind / id / name / version / when / budget_han
  kind 只能是 skill（包根）或 task（子卡）
  子卡的 skill 必须指向包里的父卡
  id 不能和现有卡撞（撞了要 --force 才覆盖）
"""

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ["HER_HOME"]).resolve() if os.environ.get("HER_HOME") else ROOT
SKILLS = DATA / "skills"
SCHEMA = 1
REQUIRED = ["kind", "id", "name", "version", "when", "budget_han"]


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def parse_fm(text):
    """→ (frontmatter dict, body)"""
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    fm, body = m.group(1), m.group(2)
    d = {}
    for line in fm.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line.startswith(" "):
            continue
        k, _, v = line.partition(":")
        d[k.strip()] = v.strip()
    return d, body


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def all_cards():
    out = {}
    if not SKILLS.exists():
        return out
    for p in sorted(SKILLS.glob("*.md")):
        if p.name.startswith("_"):
            continue
        fm, _ = parse_fm(p.read_text(encoding="utf-8"))
        out[fm.get("id") or p.stem] = (p, fm)
    return out


def children_of(cid, cards):
    return [k for k, (_, fm) in cards.items() if fm.get("kind") == "task" and fm.get("skill") == cid]


# ---------------------------------------------------------------- export
def cmd_export(a):
    cards = all_cards()
    if a.id not in cards:
        print(f"[X] 没有这张卡：{a.id}（现有：{'、'.join(sorted(cards))}）")
        return 2
    p, fm = cards[a.id]
    if fm.get("kind") != "skill":
        print(f"[X] 「{a.id}」不是技能卡（kind={fm.get('kind')}）；任务卡跟着父卡走，不单独导出")
        return 2

    kids = children_of(a.id, cards)
    files = {}
    out = Path(a.out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for cid in [a.id] + kids:
            src = cards[cid][0]
            blob = src.read_bytes()
            arc = f"skills/{cid}.md"
            z.writestr(arc, blob)
            files[arc] = sha_bytes(blob)
        meta = {
            "schema": SCHEMA, "kind": "skill-pack",
            "id": a.id, "name": fm.get("name", a.id), "version": fm.get("version", ""),
            "children": kids, "exported_at": now(), "files": files,
        }
        z.writestr("_技能卡包.json", json.dumps(meta, ensure_ascii=False, indent=2))

    print(f"[OK] 导出技能卡 {a.id} → {out}")
    print(f"     文件 {len(files)} 个｜子卡：{'、'.join(kids) if kids else '无'}")
    return 0


# ---------------------------------------------------------------- inspect
def read_pack(path):
    p = Path(path)
    if p.is_file() and p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            meta = json.loads(z.read("_技能卡包.json").decode("utf-8")) if "_技能卡包.json" in z.namelist() else None
            return meta, {n: z.read(n) for n in z.namelist() if not n.endswith("/")}
    if p.is_dir():
        meta = None
        mf = p / "_技能卡包.json"
        if mf.exists():
            meta = json.loads(mf.read_text(encoding="utf-8"))
        files = {f.relative_to(p).as_posix(): f.read_bytes() for f in p.rglob("*") if f.is_file()}
        return meta, files
    return None, None


def cmd_inspect(a):
    meta, files = read_pack(a.path)
    if files is None:
        print(f"[X] 读不出：{a.path}")
        return 2
    mds = {k: v for k, v in files.items() if k.endswith(".md")}
    probs, info = [], []

    if not mds:
        probs.append("包里没有 .md 卡文件")
    parsed = {}
    for arc, blob in mds.items():
        fm, body = parse_fm(blob.decode("utf-8", errors="replace"))
        cid = fm.get("id") or Path(arc).stem
        parsed[cid] = (fm, body, arc)
        for k in REQUIRED:
            if not fm.get(k):
                probs.append(f"{arc}：缺 `{k}`")
        if fm.get("kind") not in ("skill", "task"):
            probs.append(f"{arc}：kind 只能是 skill / task（现在是 {fm.get('kind')!r}）")
        if not body.strip():
            probs.append(f"{arc}：正文是空的")

    roots = [k for k, (fm, _, _) in parsed.items() if fm.get("kind") == "skill"]
    if len(roots) != 1:
        probs.append(f"包里应当正好有 1 张 skill 卡，现在 {len(roots)} 张")
    for k, (fm, _, arc) in parsed.items():
        if fm.get("kind") == "task":
            parent = fm.get("skill")
            if not parent:
                probs.append(f"{arc}：任务卡缺 `skill`（绑哪张父卡）")
            elif parent not in parsed:
                probs.append(f"{arc}：父卡「{parent}」不在这个包里")
        if k in all_cards():
            info.append(f"本机已有同 id 的卡：{k}（导入会要求 --force）")

    if meta:
        for arc, digest in (meta.get("files") or {}).items():
            blob = files.get(arc)
            if blob is None:
                probs.append(f"包里缺文件：{arc}")
            elif sha_bytes(blob) != digest:
                probs.append(f"文件被改过（sha 不符）：{arc}")

    print(f"=== 检查：{a.path} ===")
    print(f"  文件 {len(files)} 个｜卡 {len(parsed)} 张｜父卡：{'、'.join(roots) or '（无）'}")
    for x in info:
        print(f"  [提示] {x}")
    if probs:
        print(f"  [不合规范] {len(probs)} 处：")
        for x in probs:
            print(f"    - {x}")
        return 1
    print("  [OK] 符合规范，可直接 import")
    return 0


# ---------------------------------------------------------------- import
def cmd_import(a):
    meta, files = read_pack(a.path)
    if files is None:
        print(f"[X] 读不出：{a.path}")
        return 2
    mds = {k: v for k, v in files.items() if k.endswith(".md")}
    if not mds:
        print("[X] 包里没有卡文件")
        return 2

    parsed = {}
    for arc, blob in mds.items():
        fm, body = parse_fm(blob.decode("utf-8", errors="replace"))
        cid = fm.get("id") or Path(arc).stem
        parsed[cid] = (fm, blob)

    existing = all_cards()
    clash = [k for k in parsed if k in existing]
    if clash and not a.force:
        print(f"[X] 已存在同 id 的卡：{'、'.join(clash)}；要覆盖加 --force")
        for k in clash:
            print(f"    现有：skills/{k}.md")
        return 3

    SKILLS.mkdir(parents=True, exist_ok=True)
    written = []
    for cid, (fm, blob) in parsed.items():
        target = SKILLS / f"{cid}.md"
        target.write_bytes(blob)
        written.append(cid)

    print(f"[OK] 导入 {len(written)} 张卡：{'、'.join(written)}")
    roots = [k for k, (fm, _) in parsed.items() if fm.get("kind") == "skill"]
    if roots:
        print(f"     装它：/load <角色> {roots[0]}，或在设置里选这张技能卡")
    print("     下一步：python 脚本/her-doctor.py 确认")
    return 0


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="技能卡包：导出 / 检查 / 导入")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export")
    p.add_argument("id")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("inspect")
    p.add_argument("path")
    p.set_defaults(fn=cmd_inspect)

    p = sub.add_parser("import")
    p.add_argument("path")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_import)

    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
