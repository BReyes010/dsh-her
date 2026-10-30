#!/usr/bin/env python3
"""新建人格卡：卡目录 + 记忆套件（新建或挂已有）。

用法：
  python new_card.py --id demo --name "示例角色"                 # 新卡 + 新空套件
  python new_card.py --id demo2 --name "共享记忆" --kit dafeiyu-template   # 新卡 + 挂已有套件
  python new_card.py --id demo3 --name "只建目录" --kit none       # 只建卡目录，不建套件（须自己补）

做的事：
  1. 从 cards/_template/ 复制到 cards/<id>/，替换 {id}/{name}/{memory_id}
  2. --kit new（默认）→ 建 memory/<id>/ 空套件（db + L3 骨架 + 视图）
     --kit <已有id>   → 只改 card.json 的 memory_id
     --kit none       → 不建套件
  3. 追加两张注册表
"""

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
CARDS = DATA / "cards"
MEM = DATA / "memory"
sys.path.insert(0, str(ROOT / "脚本"))

IDX = "ep,date,topic,chars,sentences,scan_status,violations,draft,review,event,scan,l1_used,status\n"


def load(p, default):
    return json.loads(Path(p).read_text(encoding="utf-8-sig")) if Path(p).exists() else default


def save(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def make_kit(kid, label):
    import memory_db as mdb

    d = MEM / kid
    (d / "L3" / "2026").mkdir(parents=True, exist_ok=True)
    (d / "L3" / "归档").mkdir(parents=True, exist_ok=True)
    (d / "L3" / "handoff").mkdir(parents=True, exist_ok=True)
    (d / "L3" / "index.csv").write_text(IDX, encoding="utf-8")
    # 2026-10-30：墓碑机制已废，不再建空文件
    (d / "L3" / "L0-changelog.md").write_text(
        "# L0 变更记录\n\n| 版本 | 日期 | 理由 | 汉字 | 估算 token |\n|---|---|---|---|---|\n", encoding="utf-8")
    con = sqlite3.connect(d / "memory.db")
    con.executescript(mdb.SCHEMA)
    con.commit()
    con.close()
    import l1 as l1mod
    con = sqlite3.connect(d / "l1.db")
    con.executescript(l1mod.SCHEMA)
    con.commit()
    con.close()
    (d / "记忆.md").write_text(f"# 记忆（{label}）\n\n> 空套件，尚未写入任何条目。\n", encoding="utf-8")
    (d / "kit.json").write_text(json.dumps({
        "id": kid, "kind": "template", "label": label, "db": "memory.db", "l1": "l1.db",
        "l3": "L3/", "views": ["记忆.md"], "cards": [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--kit", default="new", help="new（默认）/ <已有套件id> / none")
    ap.add_argument("--scene", action="append", help="额外场景名，可重复")
    ap.add_argument("--from", dest="src", default="",
                    help="从哪张卡复制：卡 id（现役指针卡也支持）/ 默认 _template")
    a = ap.parse_args()

    cid = a.id.strip()
    if (CARDS / cid).exists():
        sys.exit(f"卡已存在：cards/{cid}")
    reg = load(CARDS / "_registry.json", {"default_card": cid, "cards": []})
    mreg = load(MEM / "_registry.json", {"kits": []})
    if any(c["id"] == cid for c in reg["cards"]):
        sys.exit(f"注册表里已有这个 id：{cid}")

    mid = cid if a.kit in ("new", "none") else a.kit
    dst = CARDS / cid
    src_id = a.src or "_template"
    src = CARDS / src_id
    live = None
    if a.src and not src.exists():
        # 现役卡是「指针卡」：cards/ 下没有目录，真实文件在 人格/ + 工作/
        reg_card = next((c for c in reg["cards"] if c["id"] == a.src), None)
        if not reg_card or reg_card.get("kind") != "live":
            sys.exit(f"要复制的源卡不存在：cards/{a.src}")
        live = reg_card
        src = CARDS / "_template"
    shutil.copytree(src, dst)

    if live:
        # 用现役的真实内容覆盖：内核 + 场景文件
        persona = DATA / "人格" / "人格卡.md"
        if persona.exists():
            (dst / "persona.md").write_text(persona.read_text(encoding="utf-8-sig"), encoding="utf-8")
        work = ROOT / "工作" / "口播投影.md"
        tech = ROOT / "工作" / "口播技术规范.md"
        if work.exists():
            (dst / "scenes" / "口播").mkdir(parents=True, exist_ok=True)
            (dst / "scenes" / "口播" / "work.md").write_text(work.read_text(encoding="utf-8-sig"), encoding="utf-8")
        if tech.exists():
            (dst / "scenes" / "口播" / "tech.md").write_text(tech.read_text(encoding="utf-8-sig"), encoding="utf-8")
    if a.src:   # 从真实卡复制时，去掉模板横幅、换 name/memory_id
        cj = dst / "card.json"
        if cj.exists():
            card0 = json.loads(cj.read_text(encoding="utf-8-sig"))
            card0.update({"id": cid, "name": a.name, "kind": "template", "memory_id": mid})
            save(cj, card0)
    for f in dst.rglob("*"):
        if f.is_file() and f.suffix in (".md", ".json"):
            t = f.read_text(encoding="utf-8")
            f.write_text(t.replace("{id}", cid).replace("{name}", a.name).replace("{memory_id}", mid), encoding="utf-8")

    # 额外场景
    if a.scene:
        card = json.loads((dst / "card.json").read_text(encoding="utf-8"))
        for s in a.scene:
            (dst / "scenes" / s).mkdir(parents=True, exist_ok=True)
            (dst / "scenes" / s / "work.md").write_text(f"# {s} 材料\n\n> 这个场景专属的内容。\n", encoding="utf-8")
            card["scenes"].append({"id": s, "label": s, "files": ["persona.md", f"scenes/{s}/work.md"], "memory_scene": "工作"})
        save(dst / "card.json", card)

    if a.kit == "new":
        make_kit(cid, a.name)
        mreg["kits"].append({"id": cid, "kind": "template", "label": a.name, "dir": cid})
        save(MEM / "_registry.json", mreg)
        kit_msg = f"新建记忆套件 memory/{cid}/"
    elif a.kit == "none":
        kit_msg = "未建套件（记得自己补，否则 --check 会报缺件）"
    else:
        if not any(k["id"] == a.kit for k in mreg["kits"]):
            sys.exit(f"套件不存在：{a.kit}")
        kit_msg = f"挂到已有套件 {a.kit}"

    reg["cards"].append({"id": cid, "name": a.name, "kind": "template", "dir": cid,
                         "memory_id": mid, "note": "新建"})
    save(CARDS / "_registry.json", reg)

    print(f"[OK] 卡：cards/{cid}/（id={cid}，默认套件={mid}）")
    print(f"[OK] {kit_msg}")
    print("\n下一步：")
    print(f"  1. 写 cards/{cid}/persona.md（内核：身份·动机·称呼·性格·底线）")
    print(f"  2. 按需改 scenes/ 下的场景材料")
    print(f"  3. 校验：python 脚本/kit.py --check")
    print(f"  4. 看解析：python 脚本/kit.py --card {cid}")


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    main()
