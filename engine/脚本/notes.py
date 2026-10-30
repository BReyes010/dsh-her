#!/usr/bin/env python3
"""notes：写「触动 / 项目思考」，**每日固定额度 + 超额进待审队列**。

额度（每日）：触动 5 句 · 项目 1 个
超额不拒稿，转进 `待审核笔记.jsonl`，由 maker 审：approve 才进正文，reject 记理由。

用法：
  python 脚本/notes.py add --kind 触动 --text "原话" --source "谁/哪" --why "为什么"
  python 脚本/notes.py add --kind 项目 --name "项目名" --text "一句话"
  python 脚本/notes.py pending
  python 脚本/notes.py approve --id 3
  python 脚本/notes.py reject --id 4 --why "不够触动"
  python 脚本/notes.py stat
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
L3 = DATA / "记忆" / "L3"
TOUCH = L3 / "触动.md"
PROJ = L3 / "项目思考.md"
PENDING = L3 / "待审核笔记.jsonl"

CAP = {"触动": 5, "项目": 1}
TOUCH_CHAR_CAP = 120


def han(t):
    return sum(1 for c in t if "\u4e00" <= c <= "\u9fff")


def read(p):
    return p.read_text(encoding="utf-8") if p.exists() else ""


def today():
    return date.today().isoformat()


def load_pending():
    if not PENDING.exists():
        return []
    out = []
    for line in PENDING.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def save_pending(items):
    PENDING.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items), encoding="utf-8")


def next_id(items):
    return max([x.get("id", 0) for x in items] + [0]) + 1


def append_touch(text, source, why):
    body = read(TOUCH)
    head = f"## {today()}"
    if head not in body:
        body = body.rstrip() + f"\n\n{head}\n"
    line = f"- 「{text}」｜出自：{source}｜为什么：{why}\n"
    TOUCH.write_text(body.rstrip() + "\n" + line, encoding="utf-8")


def append_project(name, text):
    body = read(PROJ)
    sec = f"""

## {name}（{date.today():%Y-%m}）

**要解决什么**：{text or '（一句话）'}

**走过的岔路**

**试过没成的**

**取舍**

**留下的原则**
"""
    PROJ.write_text(body.rstrip() + sec, encoding="utf-8")


def count_today(kind):
    if kind == "触动":
        body = read(TOUCH)
        head = f"## {today()}"
        part = body.split(head)[-1] if head in body else ""
        part = part.split("\n## ")[0]
        return len(re.findall(r"^- 「", part, re.M))
    return len(re.findall(rf"^## .+（{date.today():%Y-%m}）$", read(PROJ), re.M))



def reindex():
    """写完立刻增量更新检索索引——不然刚记的那条要等收尾才搜得到。"""
    import subprocess
    idx = ROOT / "脚本" / "l3_index.py"
    if not idx.exists():
        return
    subprocess.run([sys.executable, str(idx), "build"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace")

def cmd_add(a):
    kind = a.kind
    text = (a.text or "").strip()
    if not text:
        sys.exit("--text 不能为空")

    if kind == "触动":
        if not a.source or not a.why:
            sys.exit("触动必须带 --source（出自谁/哪）和 --why（为什么触动）——否则就是抄书")
        n = han(text)
        if n > TOUCH_CHAR_CAP:
            sys.exit(f"这句 {n} 汉字，超过单句上限 {TOUCH_CHAR_CAP}")
        payload = {"kind": kind, "text": text, "source": a.source, "why": a.why}
    else:
        if not a.name:
            sys.exit("项目要带 --name")
        dup = f"## {a.name}" in read(PROJ) or any(
            x.get("kind") == "项目" and x.get("name") == a.name
            for x in load_pending() if x.get("status") == "待审")
        if dup:
            sys.exit(f"项目「{a.name}」已存在或已在待审队列")
        payload = {"kind": kind, "name": a.name, "text": text}

    used = count_today(kind)
    quota = CAP[kind]
    if used < quota:
        if kind == "触动":
            append_touch(text, a.source, a.why)
        else:
            append_project(a.name, text)
        reindex()
        print(f"[OK] {kind} 已写入正文（今天 {used + 1}/{quota}）")
        return

    items = load_pending()
    rec = dict(payload, id=next_id(items), status="待审", ts=today(),
               over=f"{used}/{quota}", note="超额，等 maker 审")
    items.append(rec)
    save_pending(items)
    print(f"[PENDING] {kind} 今日额度已满（{used}/{quota}），已进待审队列：#{rec['id']}")
    print(f"          maker 审：python 脚本/notes.py approve --id {rec['id']}")


def cmd_pending(a):
    items = [x for x in load_pending() if x.get("status") == "待审"]
    if not items:
        print("待审队列为空")
        return
    for x in items:
        if x["kind"] == "触动":
            print(f"#{x['id']} [触动] 「{x['text']}」｜出自：{x['source']}｜为什么：{x['why']}（{x['ts']}，超额 {x['over']}）")
        else:
            print(f"#{x['id']} [项目] {x['name']}：{x['text']}（{x['ts']}，超额 {x['over']}）")
    print(f"\n共 {len(items)} 条待审")


def cmd_approve(a):
    items = load_pending()
    hit = next((x for x in items if x["id"] == a.id and x.get("status") == "待审"), None)
    if not hit:
        sys.exit(f"没有待审的 #{a.id}")
    if hit["kind"] == "触动":
        append_touch(hit["text"], hit["source"], hit["why"])
    else:
        append_project(hit["name"], hit["text"])
    hit["status"] = "已收"
    hit["approved"] = today()
    save_pending(items)
    reindex()
    print(f"[OK] #{a.id} 已收进正文（{hit['kind']}）")


def cmd_reject(a):
    items = load_pending()
    hit = next((x for x in items if x["id"] == a.id and x.get("status") == "待审"), None)
    if not hit:
        sys.exit(f"没有待审的 #{a.id}")
    hit["status"] = "已否"
    hit["rejected"] = today()
    hit["why_rejected"] = a.why or ""
    save_pending(items)
    print(f"[OK] #{a.id} 已否（理由：{a.why or '未写'}）")


def cmd_stat(a):
    for p in (TOUCH, PROJ):
        body = read(p)
        n = len(re.findall(r"^- 「", body, re.M)) if p == TOUCH else len(re.findall(r"^## ", body, re.M))
        print(f"{p.name}: {han(body)} 汉字｜{n} 条｜{p.stat().st_size} 字节")
    pend = [x for x in load_pending() if x.get("status") == "待审"]
    print(f"待审：{len(pend)} 条｜今日额度：触动 {count_today('触动')}/{CAP['触动']}，项目 {count_today('项目')}/{CAP['项目']}")


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="写笔记（触动/项目思考），每日额度 + 超额待审")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add")
    p.add_argument("--kind", choices=["触动", "项目"], required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--source", default="")
    p.add_argument("--why", default="")
    p.add_argument("--name", default="")
    p.set_defaults(fn=cmd_add)

    sub.add_parser("pending").set_defaults(fn=cmd_pending)

    p = sub.add_parser("approve"); p.add_argument("--id", type=int, required=True); p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("reject"); p.add_argument("--id", type=int, required=True)
    p.add_argument("--why", default=""); p.set_defaults(fn=cmd_reject)

    sub.add_parser("stat").set_defaults(fn=cmd_stat)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
