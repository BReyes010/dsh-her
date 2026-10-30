#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把新记忆追加到记忆的**待确认区**。

自动化写回只做「拟好」，不做「落库」：追加的条目一律带 `待确认：True`
和 `启用：False`，因此不会被 memory_lookup.py 注入稿子，
直到人工过目后手动移入正式条目。

原因：记忆会累积，一条写歪的条目在之后每次写稿都被反复唤起，
纠偏成本比单篇稿子高得多。

用法：
    # 正文从文件读（推荐，PowerShell 传多行内容容易出问题）
    python memory_append.py --title "咕咕嘎嘎" --category "梗库" \\
        --keys "咕咕嘎嘎,企鹅" --content-file draft.md

    # 正文直接给（单行短内容）
    python memory_append.py --title "第 2 支视频" --category "成长记录" \\
        --content "这次她把长句拆开了，念起来顺了一些。"

    # 建议它常驻（仍进待确认区，只是标记出来给人工参考）
    python memory_append.py --title "当前状态" --category "常驻层" --constant

    # 干跑，只看会写成什么样，不落盘
    python memory_append.py --title "测试" --content "..." --dry-run

退出码：0 成功；2 参数或记忆文件有问题；3 标题重复（拒绝写入）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BOOK = Path(__file__).resolve().parent.parent / "记忆" / "记忆.md"
PENDING_MARKER = "## 待确认区"
BLOCK_SEP = "\n\n" + "=" * 64 + "\n"
TITLE_RE = re.compile(r"^【(.+)】$", re.MULTILINE)

# 待确认区的条目号从 101 起，与正式条目（1-99）隔离，
# 这样人工移入正式区时能一眼看出哪些是新沉淀的。
PENDING_START = 101


def existing_titles(text: str) -> set[str]:
    return {m.group(1).strip() for m in TITLE_RE.finditer(text)}


def next_pending_number(text: str) -> int:
    numbers = [
        int(m.group(1))
        for m in re.finditer(r"^第\s*(\d+)\s*条｜", text, re.MULTILINE)
    ]
    pending = [n for n in numbers if n >= PENDING_START]
    return (max(pending) + 1) if pending else PENDING_START


def build_entry(
    number: int,
    title: str,
    category: str,
    keys: list[str],
    content: str,
    constant: bool,
    source: str,
    suggestion: str,
    note: str,
) -> str:
    key_text = ("_or_" + "@wb@".join(keys)) if keys else "_or_"
    layer = "常驻" if constant else "关键词"
    order = 900 + number  # 待确认区排在所有正式条目之后

    meta = (
        f"触发区域：启用：False｜插入顺序：{order}｜"
        f"常驻：{'True' if constant else 'False'}｜待确认：True"
    )

    body = content.strip()
    tail = [
        "",
        "---",
        "",
        f"- 来源：{source}",
        f"- 建议归入：{layer}层"
        + (f"，关键词 `{'@wb@'.join(keys)}`" if keys else "，无关键词（常驻）"),
    ]
    if suggestion:
        tail.append(f"- 建议关键词：`{'@wb@'.join(keys)}`" if keys else "")
    if note:
        tail.append(f"- 待主人确认的点：{note}")
    tail.append("- 确认后：删除本小节，把 `启用：False` 改为 `True`、"
                "`待确认：True` 删除，移入上方正式条目，并更新〈条目索引〉。")

    return (
        f"第 {number} 条｜分类：{category}\n"
        f"关键词：{key_text}\n"
        f"{meta}\n"
        f"【{title}】\n\n{body}\n" + "\n".join(t for t in tail if t != "")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把新记忆追加到记忆待确认区（不直接进入正式条目）。"
    )
    parser.add_argument("--title", required=True, help="条目标题，写进【】")
    parser.add_argument(
        "--category", default="待确认", help="分类，写进条目头，默认「待确认」"
    )
    parser.add_argument(
        "--keys",
        nargs="?",
        const="",
        default="",
        help="关键词，逗号分隔。留空或省略表示建议作为常驻条目。"
        "注意 PowerShell 会吞掉空引号，所以这里用 nargs='?' 兜住。",
    )
    parser.add_argument("--content", help="正文内容（短内容用）")
    parser.add_argument("--content-file", help="正文内容文件路径（长内容推荐）")
    parser.add_argument(
        "--constant",
        action="store_true",
        help="建议这条进常驻层（每次写稿无条件注入）",
    )
    parser.add_argument(
        "--source",
        default="本次写稿过程沉淀",
        help="来源说明，默认「本次写稿过程沉淀」",
    )
    parser.add_argument("--note", default="", help="待主人确认的点，说明哪里拿不准")
    parser.add_argument("--book", default=str(BOOK), help=f"记忆路径，默认 {BOOK}")
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印将要追加的内容，不写文件"
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    if args.content and args.content_file:
        print("[error] --content 和 --content-file 只能给一个", file=sys.stderr)
        return 2
    if not args.content and not args.content_file:
        print("[error] 必须提供 --content 或 --content-file", file=sys.stderr)
        return 2

    if args.content_file:
        cpath = Path(args.content_file)
        if not cpath.exists():
            print(f"[error] 正文文件不存在：{cpath}", file=sys.stderr)
            return 2
        content = cpath.read_text(encoding="utf-8")
    else:
        content = args.content

    if not content.strip():
        print("[error] 正文为空", file=sys.stderr)
        return 2

    book = Path(args.book)
    if not book.exists():
        print(f"[error] 记忆不存在：{book}", file=sys.stderr)
        return 2

    text = book.read_text(encoding="utf-8")

    if PENDING_MARKER not in text:
        print(
            f"[error] 记忆里找不到「{PENDING_MARKER}」标记，无法定位追加位置。"
            "请先修复文件结构。",
            file=sys.stderr,
        )
        return 2

    title = args.title.strip()
    if title in existing_titles(text):
        print(
            f"[error] 已存在同名条目「{title}」。"
            "记忆条目应唯一；要补充内容请直接编辑原条目，不要新增重复项。",
            file=sys.stderr,
        )
        return 3

    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    constant = args.constant or not keys

    number = next_pending_number(text)
    entry = build_entry(
        number=number,
        title=title,
        category=args.category.strip() or "待确认",
        keys=keys,
        content=content,
        constant=constant,
        source=args.source.strip(),
        suggestion="",
        note=args.note.strip(),
    )

    if args.dry_run:
        print(f"--- 将追加为第 {number} 条（待确认区）---\n")
        print(entry)
        return 0

    # 追加到文件末尾。当前结构下待确认区是最后一节，
    # 所以文件末尾 == 待确认区末尾。
    new_text = text.rstrip("\n") + BLOCK_SEP + entry + "\n"
    book.write_text(new_text, encoding="utf-8")

    print(f"已追加第 {number} 条「{title}」到待确认区（{book}）")
    print("状态：启用：False｜待确认：True —— 不会被注入稿子，需人工过目后移入正式条目。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
