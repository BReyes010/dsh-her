#!/usr/bin/env python3
"""load_cmd —— 「载入<角色名>[ <卡名>]」这条指令的唯一实现。

语法（第一行、必须以「载入」开头）：
  载入{角色名}               → 本体（不带卡）
  载入{角色名} 口播           → 本体 + 口播卡
  载入{角色名} 口播卡         → 别名也能认（卡 id / 名字 / 文件 stem）
  载入{角色名} 无卡           → 同「载入{角色名}」
  载入{角色名} 口播+素材      → [X] 拒：素材卡不通过指令加载（她自己查记忆）

用法：
  python 脚本/load_cmd.py "载入{角色名} 口播"        # 打印整段文本（可直接贴）
  python 脚本/load_cmd.py "载入{角色名} 口播" --han   # 只报汉字数
  python 脚本/load_cmd.py --parse "载入{角色名} 口播" # 只解析，不出文本
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ["HER_HOME"]).resolve() if os.environ.get("HER_HOME") else ROOT
SKILLS = DATA / "skills"
HOST = ROOT / "入口" / "her-host.py"

PREFIX = "载入"
NONE_WORDS = {"", "无卡", "不带卡", "-"}


def cards():
    """{别名: (path, kind, id, name)}"""
    out = {}
    if not SKILLS.exists():
        return out
    for p in sorted(SKILLS.glob("*.md")):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---", t, re.S)
        fm = m.group(1) if m else ""
        def g(k):
            mm = re.search(rf"^{k}:\s*(.+)$", fm, re.M)
            return mm.group(1).strip().strip("[]") if mm else ""
        cid, name, kind = g("id") or p.stem, g("name"), g("kind")
        for alias in (cid, p.stem, name):
            if alias:
                out[alias] = (p, kind, cid, name or cid)
    return out


def parse(line):
    """→ (card_name, [card_ids], error)"""
    s = (line or "").strip()
    if not s.startswith(PREFIX):
        return None, [], "第一行要以「载入」开头"
    rest = s[len(PREFIX):].strip()
    if not rest:
        return None, [], "「载入」后面要写角色名"
    parts = rest.split()
    who = parts[0]
    tail = parts[1] if len(parts) > 1 else ""
    if tail in NONE_WORDS:
        return who, [], ""
    ids = [x for x in re.split(r"[+，,、]", tail) if x.strip()]
    return who, ids, ""


def resolve_card(who):
    """角色名 → 卡 id（认 id / 名字 / 别名；找不到返回 None）。"""
    import json as _j
    try:
        reg = _j.loads((DATA / "cards" / "_registry.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    cards = reg.get("cards", [])
    w = (who or "").strip()
    if not w:
        return None
    for c in cards:
        ids = {str(c.get("id") or ""), str(c.get("name") or ""), str(c.get("dir") or "")}
        ids |= {str(x) for x in (c.get("aliases") or [])}
        if w in ids:
            return c.get("id")
    hits = [c.get("id") for c in cards
            if any(w and (w in str(x)) for x in
                   [c.get("id"), c.get("name")] + list(c.get("aliases") or []))]
    if len(hits) == 1:
        return hits[0]
    return None


def card_names():
    import json as _j
    try:
        reg = _j.loads((DATA / "cards" / "_registry.json").read_text(encoding="utf-8"))
        return "、".join(f"{c.get('id')}（{c.get('name')}）" for c in reg.get("cards", []))
    except Exception:
        return "（读不到卡注册表）"


def read_card(path):
    t = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", t, re.S)
    body = (m.group(2) if m else t).strip()
    return "\n".join(l for l in body.splitlines() if not l.strip().startswith("<!--")).strip()


def child_tasks(parent_id):
    """子卡（kind: task 且 skill: parent_id）——装父卡时自动带上。"""
    out = []
    if not SKILLS.exists():
        return out
    for p in sorted(SKILLS.glob("*.md")):
        if p.name.startswith("_"):
            continue
        txt = p.read_text(encoding="utf-8")
        m = re.match(r"^---\r?\n(.*?)\r?\n---", txt, re.S)
        if not m:
            continue
        fm = m.group(1)
        def g(k):
            mm = re.search(rf"^{k}:\s*(.+)$", fm, re.M)
            return mm.group(1).strip().strip("[]") if mm else ""
        if g("kind") == "task" and g("skill") == parent_id:
            out.append((g("id") or p.stem, p))
    return out


def core_text(card=""):
    env = dict(os.environ)
    if card:
        env["HER_CARD"] = card
    p = subprocess.run([sys.executable, str(HOST), "--print", "日常", "--desk", "none"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if p.returncode != 0:
        raise SystemExit(f"取本体失败：{(p.stderr or p.stdout)[:200]}")
    return (p.stdout or "").strip()


def han(t):
    return sum(1 for c in t if "\u4e00" <= c <= "\u9fff")


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="载入指令")
    ap.add_argument("cmd", help='例如 "载入{角色名} 口播"')
    ap.add_argument("--han", action="store_true", help="只报汉字数")
    ap.add_argument("--parse", action="store_true", help="只解析")
    ap.add_argument("--card", default="", help="用哪张角色卡的本体（默认注册表 default_card）")
    a = ap.parse_args()

    who, ids, err = parse(a.cmd)
    if err:
        print(f"[X] {err}", file=sys.stderr)
        return 2
    cat = cards()

    def lookup(x):
        if x in cat:
            return cat[x]
        if x.endswith("卡") and x[:-1] in cat:
            return cat[x[:-1]]
        cands = [v for k, v in cat.items() if k.endswith(x) or x.endswith(k)]
        return cands[0] if len(cands) == 1 else None
    got, bad = [], []
    for cid in ids:
        hit = lookup(cid)
        if not hit:
            bad.append(f"没有这张卡：{cid}")
            continue
        path, kind, real_id, name = hit
        if kind == "task":
            parent = ""
            try:
                fm = path.read_text(encoding="utf-8").split("---")[1]
                import re as _re
                mm = _re.search(r"^skill:\s*(.+)$", fm, _re.M)
                parent = mm.group(1).strip() if mm else ""
            except Exception:
                pass
            bad.append(f"「{real_id}」是子卡（任务卡），不单独装"
                       + (f"；载入父卡「{parent}」时会自动带上" if parent else ""))
            continue
        if kind == "memory":
            bad.append(f"「{cid}」是素材卡：不通过指令加载，让她自己查记忆")
            continue
        got.append((real_id, path))
    if bad:
        print("[X] " + "；".join(bad), file=sys.stderr)
        print("    现有卡：" + "、".join(sorted({v[2] for v in cat.values()})), file=sys.stderr)
        return 3
    if len(got) > 1:
        print(f"[X] 一次只装一张卡（这次给了 {len(got)} 张）", file=sys.stderr)
        return 3

    if a.parse:
        print(f"角色：{who}｜卡：{[g[0] for g in got] or ['无卡']}")
        return 0

    _card = getattr(a, "card", "") or resolve_card(who)
    if not _card:
        print(f"[X] 没有这个角色：{who}", file=sys.stderr)
        print(f"    现有：{card_names()}", file=sys.stderr)
        return 3
    text = core_text(_card)
    if got:
        cid, path = got[0]
        body = read_card(path)
        text += (f"\n\n---\n\n# 这次带上的卡：{cid}\n\n{body}")
        kids = child_tasks(cid)
        for kid, kpath in kids:
            text += (f"\n\n## 子卡：{kid}（任务是流程，跟着父卡一起带）\n\n"
                     f"{read_card(kpath)}")
        extra = f"｜子卡：{'、'.join(k for k, _ in kids) or '无'}" if kids else ""
        text += (f"\n\n---\n装配状态：本体 + {cid}{extra}"
                 f"｜策略：技能卡≤1｜素材：无")
    else:
        text += "\n\n---\n装配状态：本体（无卡）"
    if a.han:
        print(f"{len(text)} 字符 / {han(text)} 汉字")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
