#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""{角色名}记忆检索器。

单一数据源是 `记忆.md`（同目录），本脚本直接解析它，不经过中间格式。
条目格式沿用 default-workspace 下 parse_memory.py / build_v12_memory.py
的约定，因此需要时可另写脚本导出成 SillyTavern 记忆 JSON。

设计要点见 `记忆.md` 顶部：常驻层无条件注入，关键词层按主题命中注入。
纯关键词检索在口播稿场景会大面积失效——稿子主题通常是外部话题，
不会出现「主人」「上期我讲错了」这类词——所以常驻层必须独立于关键词存在。

用法：
    python memory_lookup.py --topic "扩散模型" --topic "咕咕嘎嘎"
    python memory_lookup.py --topic "有人骂我讲错了" --out prompt_context.md
    python memory_lookup.py --list
    python memory_lookup.py --list --json

退出码：0 正常；2 记忆文件缺失或解析不到任何条目。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from paths import paths as _paths
BOOK = _paths()["memory_md"]

# 条目之间的分隔线，沿用 parse_memory.py 的 ={20,} 约定
BLOCK_SPLIT = re.compile(r"={20,}\r?\n")
ENTRY_HEAD = re.compile(r"^第\s*(\d+)\s*条｜分类：(.*)$")
TITLE_RE = re.compile(r"^【(.+)】$")
KEY_SEP = "@wb@"
OR_PREFIX = "_or_"


@dataclass
class Entry:
    number: int
    category: str
    title: str
    keys: list[str] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    content: str = ""

    @property
    def constant(self) -> bool:
        return self.meta.get("常驻", "").strip() == "True"

    @property
    def enabled(self) -> bool:
        return self.meta.get("启用", "True").strip() != "False"

    @property
    def pending(self) -> bool:
        return self.meta.get("待确认", "").strip() == "True"

    @property
    def scene(self) -> str:
        """场景标签（L2 规范 §5.2）：通用 / 日常 / 工作；缺省视为通用。"""
        v = self.meta.get("场景", "").strip()
        return v or "通用"

    @property
    def order(self) -> int:
        try:
            return int(self.meta.get("插入顺序", "0"))
        except ValueError:
            return 0

    def matches(self, topics: list[str]) -> list[str]:
        """返回命中的关键词；无命中返回空列表。"""
        hits: list[str] = []
        for topic in topics:
            t = topic.strip()
            if not t:
                continue
            for key in self.keys:
                if not key:
                    continue
                # 双向包含：主题词可能比关键词长（「有人说我讲错了」命中「讲错」），
                # 关键词也可能比主题词长（主题「AI 拟人」命中关键词「AI娘化」）。
                if key in t or t in key:
                    if key not in hits:
                        hits.append(key)
        return hits


def parse(text: str) -> list[Entry]:
    entries: list[Entry] = []

    for block in BLOCK_SPLIT.split(text):
        lines = [ln.rstrip() for ln in block.strip().splitlines()]
        if not lines:
            continue

        head = ENTRY_HEAD.match(lines[0].strip())
        if not head:
            continue

        number = int(head.group(1))
        category = head.group(2).strip()

        meta: dict[str, str] = {}
        keys: list[str] = []
        for line in lines[1:]:
            stripped = line.strip()
            if stripped.startswith("关键词："):
                key_text = stripped[len("关键词："):].strip()
                if key_text.startswith(OR_PREFIX):
                    key_text = key_text[len(OR_PREFIX):]
                keys = [k.strip() for k in key_text.split(KEY_SEP) if k.strip()]
            elif stripped.startswith("触发区域："):
                for part in stripped[len("触发区域："):].split("｜"):
                    if "：" in part:
                        k, v = part.split("：", 1)
                        meta[k.strip()] = v.strip()

        # 正文从第一个【标题】行开始，沿用 build_v12_memory.py 的取法
        content_start = next(
            (i for i, ln in enumerate(lines) if TITLE_RE.match(ln.strip())), None
        )
        if content_start is None:
            print(
                f"[warn] 第 {number} 条没有【标题】行，已跳过",
                file=sys.stderr,
            )
            continue

        title = TITLE_RE.match(lines[content_start].strip()).group(1).strip()
        content = "\n".join(lines[content_start:]).strip()

        entries.append(
            Entry(
                number=number,
                category=category,
                title=title,
                keys=keys,
                meta=meta,
                content=content,
            )
        )

    entries.sort(key=lambda e: (e.order, e.number))
    return entries


def render(entries: list[Entry], topics: list[str], show_keys: bool = False) -> str:
    """渲染成可直接塞进子代理 prompt 的文本块。"""
    parts: list[str] = []
    for entry in entries:
        header = f"【{entry.title}】"
        if show_keys:
            hits = entry.matches(topics)
            tag = "常驻" if entry.constant else "命中：" + "、".join(hits)
            header += f"（{tag}）"
        parts.append(header + "\n" + "\n".join(entry.content.splitlines()[1:]).strip())
    return "\n\n---\n\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检索{角色名}记忆，输出常驻层 + 关键词命中层。"
    )
    parser.add_argument(
        "--topic",
        action="append",
        default=[],
        help="本次稿子的主题、涉及人物、可能用到的梗。可重复传入。",
    )
    parser.add_argument(
        "--book",
        default=str(BOOK),
        help=f"记忆路径，默认 {BOOK}",
    )
    parser.add_argument("--out", help="写入文件而非打印到 stdout")
    parser.add_argument("--list", action="store_true", help="只列条目索引，不输出正文")
    parser.add_argument("--json", action="store_true", help="配合 --list 输出 JSON")
    parser.add_argument(
        "--no-constant",
        action="store_true",
        help="只输出关键词命中层，不含常驻层（调试用）",
    )
    parser.add_argument(
        "--include-pending",
        action="store_true",
        help="包含待确认区的条目（默认排除，未过目的内容不应进入稿子）",
    )
    parser.add_argument(
        "--scene",
        default="工作",
        choices=["工作", "日常", "通用", "完整"],
        help="当前投影（L2 规范 §5.2）：工作=注入 通用+工作；日常=注入 通用+日常；通用=全部",
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        # Windows 控制台默认 GBK，直接 print 中文会 UnicodeEncodeError
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    book = Path(args.book)
    if not book.exists():
        print(f"[error] 记忆不存在：{book}", file=sys.stderr)
        return 2

    all_entries = parse(book.read_text(encoding="utf-8"))
    if not all_entries:
        print(f"[error] 没有解析到任何条目：{book}", file=sys.stderr)
        return 2

    if args.list:
        rows = []
        for e in all_entries:
            rows.append(
                {
                    "number": e.number,
                    "title": e.title,
                    "category": e.category,
                    "layer": "常驻" if e.constant else "关键词",
                    "enabled": e.enabled,
                    "pending": e.pending,
                    "order": e.order,
                    "keys": e.keys,
                    "chars": len(e.content),
                }
            )
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            print(f"{'#':>4} | {'层':<4} | {'标题':<22} | 启用 | 待确认 | 关键词数 | 字数")
            print("-" * 78)
            for r in rows:
                print(
                    f"{r['number']:>4} | {r['layer']:<4} | {r['title']:<22} | "
                    f"{'是' if r['enabled'] else '否':<4} | "
                    f"{'是' if r['pending'] else '否':<6} | "
                    f"{len(r['keys']):>7} | {r['chars']:>5}"
                )
            print(f"\n共 {len(rows)} 条")
        return 0

    if not args.include_pending:
        all_entries = [e for e in all_entries if not e.pending]
    all_entries = [e for e in all_entries if e.enabled]
    # 场景过滤（L2 规范 §5.2）：通用两处都注入；"通用"投影则全放行
    if args.scene != "通用":
        all_entries = [e for e in all_entries if e.scene in ("通用", args.scene)]

    constant = [] if args.no_constant else [e for e in all_entries if e.constant]
    keyword_hits = [
        (e, e.matches(args.topic))
        for e in all_entries
        if not e.constant and e.matches(args.topic)
    ]

    selected = constant + [e for e, _ in keyword_hits]
    if not selected:
        print(
            "[warn] 常驻层为空且没有关键词命中。"
            "检查记忆是否配置了常驻条目，或 --topic 是否传了主题。",
            file=sys.stderr,
        )
        return 2

    topics = args.topic
    body = render(constant, topics)
    hit_body = render([e for e, _ in keyword_hits], topics, show_keys=bool(topics))
    if hit_body:
        body = body + "\n\n---\n\n" + hit_body

    header_lines = [
        "# 记忆注入内容（来自 记忆.md，只读）",
        "",
        f"常驻层 {len(constant)} 条"
        + (f"，关键词命中 {len(keyword_hits)} 条" if keyword_hits else "，关键词无命中"),
    ]
    if topics:
        header_lines.append("检索主题：" + "、".join(t.strip() for t in topics))
    if keyword_hits:
        for e, hits in keyword_hits:
            header_lines.append(f"  · {e.title} ← {'、'.join(hits)}")
    header_lines += ["", "以下是她此刻的状态和经历。写稿时当作背景事实使用，不要照抄进稿子。", ""]
    output = "\n".join(header_lines) + body + "\n"

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"已写入 {out_path}（{len(output)} 字符）")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
