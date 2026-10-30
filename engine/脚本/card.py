#!/usr/bin/env python3
"""card —— 卡的退役与归档（任务卡 / 技能卡共用）。

规则（2026-10-29 定）：
  · 完成（done）：她自己就能标，**不用审批**；标完必须提示 maker。
  · 放弃（abandoned）：她可以标，但**必须写原因**。
  · 删除/退役（retired）与恢复（restore）：**要 maker 同意**（`--by maker`），否则拒。
  · 一律**归档不删**：卡正文进 `记忆/L3/任务卡归档/`，账进 `_归档.json`；可 `l3.py grep` 查到。
  · 被实例引用的卡不许退役：先收实例。

用法：
  python 脚本/card.py list
  python 脚本/card.py status 口播交付
  python 脚本/card.py done 口播交付                          # 她可做，免审
  python 脚本/card.py retire 口播交付 --as abandoned --why "…"  # 她可做，必须写原因
  python 脚本/card.py retire 口播 --as retired --why "…" --by maker   # 删除/强制退：要 maker
  python 脚本/card.py restore 口播交付 --by maker              # 恢复：要 maker
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ["HER_HOME"]).resolve() if _os.environ.get("HER_HOME") else ROOT
SKILLS = DATA / "skills"
ARCH = DATA / "记忆" / "L3" / "任务卡归档"
INDEX = ARCH / "_归档.json"
TASKS = DATA / "记忆" / "L3" / "任务"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def sha(t):
    return "sha256:" + hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]


def parse(p):
    t = p.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", t, re.S)
    if not m:
        return {"id": p.stem, "_path": p, "_raw": t, "_han": 0}
    fm, body = m.group(1), m.group(2)
    d = {"_path": p, "_raw": t, "_body": body,
         "_han": sum(1 for c in body if "\u4e00" <= c <= "\u9fff")}
    for line in fm.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line.startswith("  "):
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        d[k.strip()] = v.strip("[]") if (v.startswith("[") and v.endswith("]")) else v
    d["id"] = d.get("id") or p.stem
    return d


def load_all():
    return {d["id"]: d for d in (parse(p) for p in sorted(SKILLS.glob("*.md"))
                                 if not p.name.startswith("_"))}


def load_index():
    if not INDEX.exists():
        return {"cards": []}
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {"cards": []}


def save_index(d):
    ARCH.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def instances(cid):
    if not TASKS.exists():
        return []
    out = []
    for p in TASKS.glob("*.jsonl"):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("task") == cid or rec.get("card") == cid:
                    out.append((p.name, rec))
        except Exception:
            continue
    return out


def cmd_list(_):
    cat = load_all()
    if not cat:
        print("（skills/ 里没有卡）")
        return
    print(f"{'ID':<12}{'kind':<8}{'slot':<9}{'汉字':>6}  名称")
    for cid, c in sorted(cat.items()):
        print(f"{cid:<12}{c.get('kind',''):<8}{c.get('slot','none'):<9}{c['_han']:>6}  {c.get('name','')}")
    idx = load_index()
    if idx["cards"]:
        print(f"\n已归档 {len(idx['cards'])} 张：" + "、".join(x["id"] for x in idx["cards"][-5:]))


def cmd_status(a):
    cat = load_all()
    c = cat.get(a.id)
    if not c:
        print(f"没有这张卡：{a.id}")
        return 2
    ins = instances(a.id)
    print(f"{c['id']}｜{c.get('name','')}｜kind={c.get('kind')}｜slot={c.get('slot','none')}")
    print(f"  汉字 {c['_han']}｜文件 {c['_path'].name}｜sha {sha(c['_raw'])}")
    if c.get("skill"):
        print(f"  挂在技能卡：{c['skill']}")
    print(f"  实例引用：{len(ins)} 条" + ("（不许退役，先收实例）" if ins else "（可退役）"))
    return 0


def archive(c, status, why, by):
    ARCH.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    dst = ARCH / f"{c['id']}@v{c.get('version','1')}-{stamp}.md"
    if dst.exists():
        dst = ARCH / f"{c['id']}@v{c.get('version','1')}-{stamp}-2.md"
    shutil.move(str(c["_path"]), str(dst))
    idx = load_index()
    idx["cards"].append({
        "id": c["id"], "name": c.get("name", ""), "kind": c.get("kind", ""),
        "version": c.get("version", "1"), "status": status, "why": why, "by": by,
        "archived_at": now(), "file": dst.name, "sha": sha(c["_raw"]), "han": c["_han"],
    })
    save_index(idx)
    return dst


def cmd_retire(a):
    cat = load_all()
    c = cat.get(a.id)
    if not c:
        print(f"没有这张卡：{a.id}")
        return 2
    if a.as_ in ("abandoned", "retired") and not a.why.strip():
        print("放弃或强制退役必须写原因（--why）：不写原因的退役＝抹掉为什么", file=sys.stderr)
        return 3
    # 删除/强制退役 要 maker
    if a.as_ == "retired" and a.by != "maker":
        print("删除（retired）要 maker 同意：加 --by maker", file=sys.stderr)
        return 3
    ins = instances(a.id)
    if ins:
        print(f"这张卡还有 {len(ins)} 条实例引用，先收实例再退役", file=sys.stderr)
        return 3
    dst = archive(c, a.as_, a.why or "", a.by)
    print(f"[已归档] {c['id']} → {dst.relative_to(DATA)}")
    print(f"【提示】请告诉 maker：任务卡「{c['id']}」已标记 {a.as_}"
          + (f"（原因：{a.why}）" if a.why else ""))
    print("  归档可查：python 脚本\\l3.py grep " + c["id"])
    return 0


def cmd_done(a):
    """完成：她自己就能做，不用审批。"""
    a.as_ = "done"
    a.why = a.why or "任务完成"
    a.by = a.by or "她"
    return cmd_retire(a)


def cmd_restore(a):
    if a.by != "maker":
        print("恢复要 maker 同意：加 --by maker", file=sys.stderr)
        return 3
    idx = load_index()
    hit = [x for x in idx["cards"] if x["id"] == a.id]
    if not hit:
        print(f"归档里没有 {a.id}")
        return 2
    rec = hit[-1]
    src = ARCH / rec["file"]
    if not src.exists():
        print(f"归档文件不见了：{rec['file']}")
        return 2
    dst = SKILLS / f"{rec['id']}.md"
    if dst.exists():
        print(f"skills/ 里已经有同名卡：{dst.name}（先改 id）", file=sys.stderr)
        return 3
    shutil.move(str(src), str(dst))
    idx["cards"] = [x for x in idx["cards"] if x is not rec]
    save_index(idx)
    print(f"[已恢复] {rec['id']} → skills/{dst.name}")
    print(f"【提示】请告诉 maker：任务卡「{rec['id']}」已从归档恢复")
    return 0


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="卡的退役与归档")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("status"); p.add_argument("id"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("done"); p.add_argument("id"); p.add_argument("--why"); p.add_argument("--by", default="她"); p.set_defaults(fn=cmd_done)
    p = sub.add_parser("retire")
    p.add_argument("id")
    p.add_argument("--as", dest="as_", default="abandoned", choices=["done", "abandoned", "retired"])
    p.add_argument("--why", default="")
    p.add_argument("--by", default="她")
    p.set_defaults(fn=cmd_retire)
    p = sub.add_parser("restore"); p.add_argument("id"); p.add_argument("--by", default=""); p.set_defaults(fn=cmd_restore)
    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
