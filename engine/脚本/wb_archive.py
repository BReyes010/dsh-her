#!/usr/bin/env python3
"""wb_archive：把任务看板 json 里 status != active 的条目归档到 L3（幂等整份重写）。

产物：
  记忆/L3/归档/任务看板-已作废.md    ← 正文 + 索引表（人看、可 grep）
  记忆/L3/归档/_归档.json          ← 校验用：条数、sha256、生成时间

规矩（{角色名}定的）：
  · 五样齐全：编号 / 原文 / 理由 / 日期 / 替代→
  · 追加式会留旧副本，所以这里**整份重写**，重跑结果一致
  · 写完记 sha256，import 和 render 都要先校验
"""

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from paths import rel

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
JS = DATA / "入口" / "任务看板.json"
OUT_DIR = DATA / "记忆" / "L3" / "归档"
OUT = OUT_DIR / "任务看板-已作废.md"
META = OUT_DIR / "_归档.json"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def keywords(text, limit=3):
    """粗提关键词：按标点切，取前几段里最长的词块。不求准，只求能搜到。"""
    parts = [p.strip() for p in re.split(r"[，。；：、（）()`\s→]+", text) if len(p.strip()) >= 2]
    head = parts[:limit]
    return ",".join(head) if head else text[:12]


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    if not JS.exists():
        sys.exit(f"找不到 {rel(JS)}")

    force = "--force" in sys.argv

    d = json.loads(JS.read_text(encoding="utf-8"))
    allitems = d.get("decisions", [])
    retired = [x for x in allitems if x.get("status") != "active"]

    # 替代链：同 id 的 active 条目？通常没有；退而求其次记录"当前同类里最新的 active"
    active_ids = [x["id"] for x in allitems if x.get("status") == "active"]

    # 宁可报错，不许清空：源里没有 retired，但归档里已有内容 → 拒绝覆盖
    if not force and not retired and META.exists():
        try:
            old_n = json.loads(META.read_text(encoding="utf-8-sig")).get("count", 0)
        except Exception:
            old_n = 0
        if old_n > 0:
            print(f"[X] 拒绝归档：json 里 retired 为 0 条，但归档现有 {old_n} 条。", file=sys.stderr)
            print("    这会把 {0} 条历史清空。确要清空请加 --force。".format(old_n), file=sys.stderr)
            sys.exit(3)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 任务看板-已作废（归档 · 只读）",
        "",
        "> 来源：`入口/任务看板.json` 里 `status != active` 的条目。",
        "> 由 `脚本/wb_archive.py` 整份生成，**请勿手改**；改了 sha 校验会失败并拦下 import/render。",
        f"> 生成：{datetime.now():%Y-%m-%d %H:%M} ｜ 条目：{len(retired)} 条",
        "",
        "## 索引（按编号 / 关键词可查：`python 脚本/wb_query.py <词>`）",
        "",
        "| 编号 | 关键词 | 一句话 | 替代 |",
        "|---|---|---|---|",
    ]
    for x in retired:
        kw = keywords(x.get("text", ""))
        one = (x.get("text", "").split("：")[0])[:28]
        rep = x.get("replaced_by") or ""
        lines.append(f"| {x['id']} | {kw} | {one} | {rep} |")

    lines += ["", "## 条目", ""]
    for x in retired:
        lines += [
            f"### {x['id']}",
            "",
            f"- 原文：{x.get('text','').strip()}",
            f"- 理由：{(x.get('why') or '（未记）').strip()}",
            f"- 日期：{(x.get('retired_at') or x.get('date') or '').strip()}",
            f"- 替代：{x.get('replaced_by') or '（未记）'}",
            "",
        ]

    text = "\n".join(lines).rstrip() + "\n"
    # newline="\n"：不跟平台走，写进去的字节 == 后面算 sha 的字节
    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    # render 后 json 会重写，sha 只对归档文件本身，不含时间行
    body_for_sha = re.sub(r"^> 生成：.*$", "", text, flags=re.M)
    meta = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": len(retired),
        "ids": [x["id"] for x in retired],
        "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
        "file": str(rel(OUT)),
    }
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] 归档 {len(retired)} 条 → {rel(OUT)}")
    print(f"     索引 {len(retired)} 行｜sha256 {meta['sha256'][:12]}")
    if active_ids:
        print(f"     （当前 active {len(active_ids)} 条：{', '.join(active_ids)}）")


if __name__ == "__main__":
    main()
