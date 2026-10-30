#!/usr/bin/env python3
"""daily_quotes：日常原话（上一次聊天说过的三句）——**L2 层的临时缓冲**，不是 L3。

为什么单独一个文件：日常聊天默认不进 L3（B 档），但「上一轮说了什么」是开场最缺的一格。
所以只留**原话**、只留最近几天，用完即弃——这是**临时记忆**，不是永久记忆。

规矩：
  · 每次收尾最多写 3 句，每句 ≤40 汉字（超了拒绝，不截断）；
  · 只记原话（谁说的 + 内容），不许摘要、不许合并；
  · 不记工作内容（口播/选题/播放数据/待办）；
  · **清理机制**：默认只保留最近 60 天；更早的整节移进 `日常原话-归档.md`（带 sha 记账），
    主文件永远只有最近两个月——注入只读最近一节，所以体积与注入量无关。

用法：
  python 脚本/daily_quotes.py add --who 他 --text "…"
  python 脚本/daily_quotes.py list [--limit 5]
  python 脚本/daily_quotes.py prune --dry-run
  python 脚本/daily_quotes.py prune            # 真移（默认 60 天）
  python 脚本/daily_quotes.py stat
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
L3 = ROOT / "记忆" / "L3"
L2 = ROOT / "记忆"
MAIN = L2 / "日常原话.md"
ARCH = L2 / "日常原话-归档.md"
META = L2 / "日常原话.json"

KEEP_DAYS = 7
PER_DAY = 3
CHAR_CAP = 40
WHO = ("他", "我", "她")

HEADER = """# 日常原话

> **只留原话，最近 7 天。** 一句一行：`- 他：「…」`
> 每次收尾最多 3 句、每句 ≤40 汉字；不摘要、不合并、不记工作内容。
> 超过 7 天的整节会被清掉（只记「哪天、几句、sha256」，不留内容）——这是临时缓冲，不是永久记忆。
> 想留档就 `prune --archive`，那会移进 `日常原话-归档.md`。

"""


def han(t):
    return sum(1 for c in t if "\u4e00" <= c <= "\u9fff")


def read(p):
    return p.read_text(encoding="utf-8") if p.exists() else ""


def sections(text):
    """{日期: [行]}"""
    out = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^## (\d{4}-\d{2}-\d{2})\s*$", line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
        elif cur and line.strip().startswith("- "):
            out[cur].append(line.rstrip())
    return out


def rebuild(secs, header=True):
    body = HEADER if header else ""
    for d in sorted(secs):
        if not secs[d]:
            continue
        body += f"## {d}\n" + "\n".join(secs[d]) + "\n\n"
    return body.rstrip() + "\n"


def cmd_add(a):
    who = a.who
    if who not in WHO:
        sys.exit(f"--who 只能是 {'/'.join(WHO)}")
    text = (a.text or "").strip().strip("「」")
    if not text:
        sys.exit("--text 不能为空")
    n = han(text)
    if n > CHAR_CAP:
        sys.exit(f"这句 {n} 汉字，超过单句上限 {CHAR_CAP}——挑最要紧的半句")
    if re.search(r"播放|点赞|硬币|涨粉|字数|待办|签字|第\s*\d+\s*期", text):
        sys.exit("这句像工作内容（播放/期数/待办）——日常原话不记工作")

    text_all = read(MAIN)
    secs = sections(text_all)
    today = date.today().isoformat()
    secs.setdefault(today, [])
    if len(secs[today]) >= PER_DAY:
        sys.exit(f"今天已经记了 {len(secs[today])} 句，超过 {PER_DAY} 句/次的上限")
    secs[today].append(f"- {who}：「{text}」")
    MAIN.write_text(rebuild(secs), encoding="utf-8")
    print(f"[OK] 日常原话 +1（今天 {len(secs[today])}/{PER_DAY}，{n} 汉字）")
    reindex()


def reindex():
    import subprocess
    idx = ROOT / "脚本" / "l3_index.py"
    if idx.exists():
        subprocess.run([sys.executable, str(idx), "build"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")


def cmd_list(a):
    secs = sections(read(MAIN))
    if not secs:
        print("（还没有日常原话）")
        return
    for d in sorted(secs)[-a.limit:]:
        print(f"## {d}")
        for line in secs[d]:
            print(line)
    total = sum(len(v) for v in secs.values())
    print(f"\n共 {total} 句，跨 {len(secs)} 天")


def cmd_prune(a):
    """超过 KEEP_DAYS 的整节：默认**丢弃并记账**；`--archive` 则移进归档。"""
    secs = sections(read(MAIN))
    if not secs:
        print("没有需要清理的")
        return
    cutoff = date.today() - timedelta(days=a.days)
    old = {d: v for d, v in secs.items() if date.fromisoformat(d) < cutoff}
    if not old:
        print(f"最近 {a.days} 天内无需清理（共 {len(secs)} 天）")
        return
    old_lines = sum(len(v) for v in old.values())
    old_text = "\n".join(f"## {d}\n" + "\n".join(v) for d, v in sorted(old.items()))
    sha = hashlib.sha256(old_text.encode("utf-8")).hexdigest()
    action = "归档" if a.archive else "丢弃"
    print(f"[{'dry-run' if a.dry_run else action}] {len(old)} 天 / {old_lines} 句（sha {sha[:12]}）")
    for d in sorted(old):
        print(f"   {d}  {len(old[d])} 句")
    if a.dry_run:
        return

    if a.archive:
        arch_secs = sections(read(ARCH))
        for d, v in old.items():
            arch_secs.setdefault(d, [])
            for line in v:
                if line not in arch_secs[d]:
                    arch_secs[d].append(line)
        ARCH.write_text(rebuild(arch_secs), encoding="utf-8")

    keep = {d: v for d, v in secs.items() if d not in old}
    MAIN.write_text(rebuild(keep), encoding="utf-8")

    meta = {}
    if META.exists():
        try:
            meta = json.loads(META.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    history = meta.get("history") or []
    history.append({"pruned_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "keep_days": a.days, "action": action,
                    "days": sorted(old), "lines": old_lines, "sha256": sha})
    meta.update({
        "pruned_at": history[-1]["pruned_at"],
        "keep_days": a.days,
        "action": action,
        "moved_days": sorted(old),
        "moved_lines": old_lines,
        "content_sha256": sha,
        "history": history[-50:],
    })
    if a.archive:
        meta["archive_sha256"] = hashlib.sha256(ARCH.read_bytes()).hexdigest()
        meta["archive_lines"] = sum(len(v) for v in sections(read(ARCH)).values())
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 已{action}；主文件剩 {len(keep)} 天 / {sum(len(v) for v in keep.values())} 句")
    reindex()


def cmd_stat(a):
    secs = sections(read(MAIN))
    arch = sections(read(ARCH))
    print(f"日常原话：{sum(len(v) for v in secs.values())} 句 / {len(secs)} 天 / {han(read(MAIN))} 汉字")
    print(f"归档：    {sum(len(v) for v in arch.values())} 句 / {len(arch)} 天 / {han(read(ARCH))} 汉字")
    if META.exists():
        try:
            m = json.loads(META.read_text(encoding="utf-8"))
            print(f"上次清理：{m.get('pruned_at')}｜保留 {m.get('keep_days')} 天｜移走 {m.get('moved_lines')} 句")
        except Exception:
            pass


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="日常原话（最近 60 天）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add"); p.add_argument("--who", required=True)
    p.add_argument("--text", required=True); p.set_defaults(fn=cmd_add)

    p = sub.add_parser("list"); p.add_argument("--limit", type=int, default=5); p.set_defaults(fn=cmd_list)

    p = sub.add_parser("prune"); p.add_argument("--days", type=int, default=KEEP_DAYS)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--archive", action="store_true", help="保留：移进 日常原话-归档.md（默认丢弃只记账）")
    p.set_defaults(fn=cmd_prune)

    sub.add_parser("stat").set_defaults(fn=cmd_stat)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
