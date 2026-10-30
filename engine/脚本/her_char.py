#!/usr/bin/env python3
"""her_char —— 角色包：导出 / 检查 / 导入。

一、导出
  python 脚本/her_char.py export <角色id> --out {角色名}.zip [--with-history]

二、检查（不导入，只看合不合规范）
  python 脚本/her_char.py inspect 某.zip
  python 脚本/her_char.py inspect 某个目录

三、导入
  python 脚本/her_char.py import 某.zip
  python 脚本/her_char.py import 某.zip --answers answers.json   # 不规范卡：问答后补齐再导

角色包结构（schema 1）：
  _角色包.json         元信息 + 每个文件的 sha256
  cards/<id>/...       角色卡
  memory/<id>/...      记忆套件（memory.db / l1.db / L3 可选）

规范（导入会被检查的）：
  card.json 必有：id / name / version / kind / memory_id / dir / scenes[≥1]
  scenes 里每个 files 必须真实存在
  persona（scenes[0].files 里第一个 .md）必须存在且非空
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ["HER_HOME"]).resolve() if os.environ.get("HER_HOME") else ROOT
CARDS = DATA / "cards"
MEM = DATA / "memory"
SCHEMA = 1


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


# ---------------------------------------------------------------- export
def _reg_entry(cid):
    reg = load_json(CARDS / "_registry.json", {})
    for c in reg.get("cards", []):
        if c.get("id") == cid:
            return c
    return {}


def cmd_export(a):
    cid = a.id
    entry = _reg_entry(cid)
    card_dir = CARDS / cid
    live = entry.get("kind") == "live" or not card_dir.exists()

    files = {}
    out = Path(a.out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        if live:
            # live：从 人格/ 与 记忆/ 抽成可搬家的包
            persona = DATA / "人格" / "人格卡.md"
            if not persona.exists():
                print(f"[X] live 卡缺人格卡：{persona}")
                return 2
            blob = persona.read_bytes()
            z.writestr(f"cards/{cid}/persona.md", blob)
            files[f"cards/{cid}/persona.md"] = hashlib.sha256(blob).hexdigest()

            mem_src = DATA / "记忆"
            # 只带角色自己的数据（白名单），别把规范/报告/别人的对话一起搬走
            KEEP = {"memory.db", "l1.db", "kit.json", "触动.md", "项目思考.md",
                    "日常原话.md", "日常原话-归档.md", "index.csv"}
            for f in sorted(mem_src.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(mem_src).as_posix()
                if rel in ("记忆.md", "场景.md"):
                    continue          # 视图由 db 重新生成
                keep = rel in KEEP
                if a.with_history and rel.startswith("L3/") and not rel.endswith("_fts.db"):
                    keep = True
                if not keep:
                    continue
                arc = f"memory/{cid}/{rel}"
                z.write(f, arc)
                files[arc] = sha_file(f)

            cj = {
                "id": cid, "name": entry.get("name") or cid,
                "version": entry.get("version") or "v1", "kind": "template",
                "memory_id": cid, "dir": cid,
                "scenes": [{"id": (entry.get("scenes") or [{}])[0].get("id", "日常"),
                            "label": (entry.get("scenes") or [{}])[0].get("label", "日常对话"),
                            "files": ["persona.md"],
                            "memory_scene": (entry.get("scenes") or [{}])[0].get("memory_scene", "日常")}],
            }
            z.writestr(f"cards/{cid}/card.json", json.dumps(cj, ensure_ascii=False, indent=2))
            files[f"cards/{cid}/card.json"] = hashlib.sha256(
                json.dumps(cj, ensure_ascii=False, indent=2).encode()).hexdigest()
        else:
            cj = load_json(card_dir / "card.json")
            if not cj:
                print(f"[X] {card_dir/'card.json'} 读不出或不存在")
                return 2
            mid = cj.get("memory_id") or cid
            mem_dir = MEM / mid
            for base, pack_prefix in ((card_dir, f"cards/{cid}"), (mem_dir, f"memory/{mid}")):
                if not base.exists():
                    continue
                for f in sorted(base.rglob("*")):
                    if not f.is_file():
                        continue
                    rel = f.relative_to(base).as_posix()
                    if not a.with_history and (rel.startswith("L3/handoff/") or rel.startswith("L3/归档/")
                                               or rel.startswith("L3/任务")):
                        continue
                    arc = f"{pack_prefix}/{rel}"
                    z.write(f, arc)
                    files[arc] = sha_file(f)

        mid = cid if live else (load_json(card_dir / "card.json").get("memory_id") or cid)
        meta = {
            "schema": SCHEMA, "id": cid, "name": entry.get("name") or cid,
            "version": entry.get("version") or "v1", "memory_id": mid,
            "kind": "template" if live else (load_json(card_dir / "card.json").get("kind") or ""),
            "exported_at": now(), "with_history": bool(getattr(a, "with_history", False)),
            "files": files,
        }
        z.writestr("_角色包.json", json.dumps(meta, ensure_ascii=False, indent=2))

    print(f"[OK] 导出 {cid} → {out}")
    print(f"     文件 {len(files)} 个｜记忆套件 {mid}｜历史 {'带' if a.with_history else '不带'}"
          f"｜来源 {'live（人格/ + 记忆/）' if live else 'cards/'}")
    return 0


# ---------------------------------------------------------------- inspect
REQUIRED = ["id", "name", "version", "kind", "memory_id", "dir", "scenes"]


def read_pack(path):
    """→ (meta, {arcname: bytes})；不是包就返回 (None, None)。"""
    p = Path(path)
    if p.is_file() and p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            meta = None
            if "_角色包.json" in z.namelist():
                meta = json.loads(z.read("_角色包.json").decode("utf-8"))
            return meta, {n: z.read(n) for n in z.namelist() if not n.endswith("/")}
    if p.is_dir():
        meta = load_json(p / "_角色包.json", None)
        files = {f.relative_to(p).as_posix(): f.read_bytes()
                 for f in p.rglob("*") if f.is_file()}
        return meta, files
    return None, None


def check_card_json(cj):
    probs = []
    for k in REQUIRED:
        if not cj.get(k):
            probs.append(f"card.json 缺字段 `{k}`")
    scenes = cj.get("scenes") or []
    if not isinstance(scenes, list) or not scenes:
        probs.append("scenes 为空（至少要一个场景，通常是「日常」）")
    else:
        for s in scenes:
            if not s.get("id"):
                probs.append("有场景缺 `id`")
            if not s.get("files"):
                probs.append(f"场景「{s.get('id','?')}」没有 files")
    return probs


def cmd_inspect(a):
    meta, files = read_pack(a.path)
    if files is None:
        print(f"[X] 读不出：{a.path}（既不是 zip 也没有 _角色包.json）")
        return 2

    probs, warns = [], []
    if meta is None:
        warns.append("没有 `_角色包.json`：能导，但要先问清 id / name / 记忆套件")
        # 裸目录：找 card.json / persona.md
        cj_raw = files.get("cards/card.json") or files.get("card.json")
        cj = json.loads(cj_raw.decode("utf-8")) if cj_raw else {}
    else:
        cj = {}
        for arc, blob in files.items():
            if arc.endswith("/card.json"):
                cj = json.loads(blob.decode("utf-8"))
                break

    if not meta and not cj:
        probs.append("既没有 `_角色包.json`，也没有 `card.json`——认不出这是什么")
    elif cj:
        probs += check_card_json(cj)
        # 场景文件存在性
        for s in (cj.get("scenes") or []):
            for f in (s.get("files") or []):
                if not any(x.endswith("/" + f) or x == f for x in files):
                    probs.append(f"场景「{s.get('id','?')}」引用的文件不存在：{f}")

    # 校验和（有元信息时）
    if meta:
        for arc, digest in (meta.get("files") or {}).items():
            blob = files.get(arc)
            if blob is None:
                probs.append(f"包里缺文件：{arc}")
            elif hashlib.sha256(blob).hexdigest() != digest:
                probs.append(f"文件被改过（sha 不符）：{arc}")

    print(f"=== 检查：{a.path} ===")
    print(f"  文件 {len(files)} 个｜schema {meta.get('schema') if meta else '（无元信息）'}")
    if cj:
        print(f"  角色：{cj.get('id','?')}｜{cj.get('name','?')}｜{cj.get('kind','?')}")
    for w in warns:
        print(f"  [待确认] {w}")
    if probs:
        print(f"  [不合规范] {len(probs)} 处：")
        for x in probs:
            print(f"    - {x}")
        print("\n结论：需要跟用户确认后再导入（用 import，会给出问题清单）")
        return 1
    print("  [OK] 符合规范，可直接 import")
    return 0


# ---------------------------------------------------------------- use / list
def cmd_list(a):
    reg = load_json(CARDS / "_registry.json", {})
    default = reg.get("default_card", "")
    cards = reg.get("cards", [])
    if not cards:
        print("（一张卡都没有）")
        return 0
    print(f"默认卡：{default or '（未设）'}")
    for c in cards:
        mark = "★" if c.get("id") == default else " "
        print(f" {mark} {c.get('id',''):<16} {c.get('name',''):<20} kind={c.get('kind','')}")
    print("\n设为默认：her_char.py use <id>")
    return 0


def cmd_use(a):
    reg = load_json(CARDS / "_registry.json", {})
    ids = {c.get("id") for c in reg.get("cards", [])}
    if a.id not in ids:
        print(f"[X] 没有这张卡：{a.id}（现有：{'、'.join(sorted(x for x in ids if x))}）")
        return 2
    old = reg.get("default_card", "")
    reg["default_card"] = a.id
    (CARDS / "_registry.json").write_text(
        json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 默认卡 {old or '（未设）'} → {a.id}")
    print("     之后不带 --card 的地方（开场 / preflight / 生成包）都会用这张")
    return 0


# ---------------------------------------------------------------- import
def ensure_registry(path, key, entry, default_id):
    reg = load_json(path, {})
    reg.setdefault(key, [])
    reg[key] = [x for x in reg[key] if x.get("id") != entry["id"]]
    reg[key].append(entry)
    if not reg.get("default_card") and key == "cards":
        reg["default_card"] = default_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def extract(files, root_dir, prefix, dest):
    n = 0
    for arc, blob in files.items():
        if not arc.startswith(prefix):
            continue
        rel = arc[len(prefix):].lstrip("/")
        if not rel or rel.startswith("_角色包.json"):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        n += 1
    return n


def cmd_import(a):
    meta, files = read_pack(a.path)
    if files is None:
        print(f"[X] 读不出：{a.path}")
        return 2

    answers = {}
    if a.answers:
        answers = load_json(a.answers, {})
        if not answers:
            print(f"[X] answers 文件读不出：{a.answers}")
            return 2

    # 找 card.json
    cj = {}
    for arc, blob in files.items():
        if arc.endswith("/card.json"):
            try:
                cj = json.loads(blob.decode("utf-8"))
            except Exception:
                cj = {}
            break

    cid = answers.get("id") or (meta or {}).get("id") or cj.get("id")
    name = answers.get("name") or (meta or {}).get("name") or cj.get("name") or cid
    mid = answers.get("memory_id") or (meta or {}).get("memory_id") or cj.get("memory_id") or cid

    questions = []
    if not cid:
        questions.append("这个角色叫什么（id，英文短名，例如 pu / yan）？")
    if not name:
        questions.append("显示名叫什么（中文）？")
    if not mid:
        questions.append("记忆套件用哪个 id（一般同角色 id）？")
    if answers.get("confirm_new_id") is None and cid and (CARDS / str(cid)).exists():
        questions.append(f"已存在同 id 的卡「{cid}」。要改名（换新 id）还是放弃？")

    if questions:
        qf = DATA / "提案" / "_待导入问题.json"
        qf.parent.mkdir(parents=True, exist_ok=True)
        qf.write_text(json.dumps({
            "pack": str(a.path), "id": cid, "name": name, "memory_id": mid,
            "questions": questions, "created": now(),
            "hint": "答完写成 answers.json（键：id/name/memory_id/confirm_new_id），再跑 import --answers answers.json",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[要确认] 这张卡不合规范/信息不全，先把问题交给用户：")
        for i, q in enumerate(questions, 1):
            print(f"  {i}. {q}")
        print(f"\n问题清单已写到：{qf}")
        print("  答完：her_char.py import <包> --answers answers.json")
        return 1

    cid = str(cid)
    if (CARDS / cid).exists() and answers.get("confirm_new_id") is not True:
        print(f"[X] 已存在 {CARDS/cid}；换 id 后再导（或用 answers 里 confirm_new_id=true 覆盖）")
        return 3

    # 解包
    card_dest = CARDS / cid
    mem_dest = MEM / str(mid)
    n1 = extract(files, ROOT, f"cards/{cid}", card_dest)
    if n1 == 0:   # 裸目录：cards/xxx 前面没有 id，直接找 card.json/persona
        for arc, blob in files.items():
            if arc.startswith("cards/") or arc.endswith(".md") or arc.endswith(".json"):
                rel = arc.split("/", 1)[1] if arc.startswith("cards/") else arc
                target = card_dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
                n1 += 1
    n2 = extract(files, ROOT, f"memory/{mid}", mem_dest)

    if n1 == 0:
        print("[X] 包里没有找到角色卡文件")
        return 2

    # 补齐 / 重写 card.json（保证符合规范）
    cj_path = card_dest / "card.json"
    cj = load_json(cj_path, {})
    cj.update({
        "id": cid,
        "name": cj.get("name") or name,
        "version": cj.get("version") or "v1",
        "kind": cj.get("kind") or "template",
        "memory_id": str(mid),
        "dir": cid,
    })
    if not cj.get("scenes"):
        persona = next((p for p in card_dest.rglob("*.md") if p.name != "README.md"), None)
        cj["scenes"] = [{"id": "日常", "label": "日常对话",
                         "files": [persona.name if persona else "persona.md"],
                         "memory_scene": "日常"}]
    card_dest.mkdir(parents=True, exist_ok=True)
    cj_path.write_text(json.dumps(cj, ensure_ascii=False, indent=2), encoding="utf-8")

    # 记忆套件兜底：没有就建空的（复用 blank）
    if not mem_dest.exists() or not any(mem_dest.iterdir()):
        blank = MEM / "blank"
        if blank.exists():
            shutil.copytree(blank, mem_dest, dirs_exist_ok=True)
    kit = mem_dest / "kit.json"
    if not kit.exists():
        mem_dest.mkdir(parents=True, exist_ok=True)
        kit.write_text(json.dumps({
            "id": str(mid), "name": f"{name} 的记忆套件", "kind": "template",
            "db": "memory.db", "l1": "l1.db", "l3": "L3", "views": ["记忆.md"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 注册
    ensure_registry(CARDS / "_registry.json", "cards", {
        "id": cid, "name": cj["name"], "kind": cj["kind"], "memory_id": str(mid),
        "dir": cid, "scenes": cj["scenes"], "version": cj["version"],
    }, cid)
    ensure_registry(MEM / "_registry.json", "kits", {
        "id": str(mid), "name": f"{name} 的记忆套件", "kind": "template",
        "db": f"{mid}/memory.db", "l1": f"{mid}/l1.db", "l3": f"{mid}/L3",
        "views": [f"{mid}/记忆.md"], "dir": str(mid),
    }, str(mid))

    print(f"[OK] 导入完成：{cid}｜{cj['name']}")
    print(f"     卡文件 {n1} 个 → {card_dest}")
    print(f"     记忆文件 {n2} 个 → {mem_dest}")
    print(f"     已注册：cards/{cid}、memory/{mid}")
    print("     下一步：python 脚本/her-doctor.py 确认；开场用 /load " + cid)
    return 0


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="角色包：导出 / 检查 / 导入")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export")
    p.add_argument("id")
    p.add_argument("--out", required=True)
    p.add_argument("--with-history", action="store_true", help="连 L3 历史一起带（默认不带）")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("inspect")
    p.add_argument("path")
    p.set_defaults(fn=cmd_inspect)

    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("use")
    p.add_argument("id")
    p.set_defaults(fn=cmd_use)

    p = sub.add_parser("import")
    p.add_argument("path")
    p.add_argument("--answers", default="")
    p.set_defaults(fn=cmd_import)

    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
