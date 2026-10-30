#!/usr/bin/env python3
"""成稿扫描器（口播硬闸）。

按 v0.5.5 与 人格卡 v10 的规范，对一篇成稿做 6 项机械检查 + 1 项计数位，
输出人读报告，或 --json 落盘给 L3 的 `#NNN-scan.json`。

用法：
  python scan.py 稿件.md
  python scan.py 稿件.md --json 记忆/L3/2026/#001-scan.json
  python scan.py 稿件.md --ep 001
  python scan.py --dir 目录           # 批量

检查项：
  1 自称      我们/鱼鱼/本鱼/大家/各位观众/朋友们
  2 禁用标点  破折号 / 分号 / 冒号 / 括号 / 波浪号 / 叹号连用
  3 阿拉伯数字
  4 句长      两种口径：句号口径 / 换气口径，超 25 字占比
  5 直播腔    家人们/宝子/绝绝子/yyds/谁懂啊/救命/姐妹们
  6 计数位    字面词表（排除元陈述、愿望句）
  7 一行一句  含 ≥2 个句末标点的行

退出码：0 全部通过；1 有违规；2 文件不存在。
"""

import argparse
import json
import re
import sys
from pathlib import Path

SELF_REF = ["我们", "鱼鱼", "本鱼", "大家", "各位观众", "朋友们"]
LIVE_WORDS = ["家人们", "宝子", "绝绝子", "yyds", "谁懂啊", "救命", "姐妹们"]
COUNT_WORDS = ["我不太确定", "我不确定", "我不知道", "可能", "大概"]  # 长词优先
PUNCT = [
    ("破折号", r"[—–]{1,2}"),
    ("分号", r"[；;]"),
    ("冒号", r"[：:]"),
    ("括号", r"[（）()]"),
    ("波浪号", r"[~～]"),
    ("叹号连用", r"[！!]{2,}"),
]
ARABIC = re.compile(r"[0-9０-９]")
SENT_END = re.compile(r"[。！？…]+")
BREATH = re.compile(r"[，、；：。！？…]+")
META = re.compile(r"(不加|不说|不用|不写|不出现|不加一句)[「\"'“]?(可能|大概)")
WISH = re.compile(r"我希望|希望我|但愿")


def load(path):
    return Path(path).read_text(encoding="utf-8")


def body_lines(text):
    """跳过元信息头/标题/引用，只扫正文。

    约定：若文件里出现独立一行 `---`，视为元信息与正文的分隔线，只扫其后内容。
    """
    lines = text.splitlines()
    start = 0
    for idx, line in enumerate(lines):
        if line.strip() == "---":
            start = idx + 1
    out = []
    for i, line in enumerate(lines[start:], start + 1):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(">") or s == "---":
            continue
        out.append((i, s))
    return out


def split_sentences(s):
    return [p.strip() for p in SENT_END.split(s) if p.strip()]


def split_breath(s):
    return [p.strip() for p in BREATH.split(s) if p.strip()]


def scan(path, ep=None):
    text = load(path)
    lines = body_lines(text)
    report = {"file": str(path), "ep": ep, "checks": {}, "violations": 0}

    # 1 自称
    hits = []
    for ln, s in lines:
        for w in SELF_REF:
            if w in s:
                hits.append({"line": ln, "word": w, "text": s[:40]})
    report["checks"]["self_ref"] = {"count": len(hits), "hits": hits}

    # 2 标点
    ph = []
    for ln, s in lines:
        for name, pat in PUNCT:
            for m in re.finditer(pat, s):
                ph.append({"line": ln, "kind": name, "char": m.group(0), "text": s[:40]})
    report["checks"]["punctuation"] = {"count": len(ph), "hits": ph}

    # 3 阿拉伯数字
    ah = []
    for ln, s in lines:
        for m in ARABIC.finditer(s):
            ah.append({"line": ln, "char": m.group(0), "text": s[:40]})
    report["checks"]["arabic"] = {"count": len(ah), "hits": ah}

    # 4 句长（两口径）
    long_period, long_breath, total_p, total_b = [], [], 0, 0
    longest = 0
    for ln, s in lines:
        for sent in split_sentences(s):
            total_p += 1
            n = len(re.sub(r"\s", "", sent))
            longest = max(longest, n)
            if n > 25:
                long_period.append({"line": ln, "len": n, "text": sent[:30]})
        for frag in split_breath(s):
            total_b += 1
            if len(re.sub(r"\s", "", frag)) > 25:
                long_breath.append({"line": ln, "len": len(frag), "text": frag[:30]})
    report["checks"]["length_period"] = {
        "over": len(long_period), "total": total_p,
        "ratio": round(len(long_period) / total_p * 100, 1) if total_p else 0,
        "longest": longest, "hits": long_period[:10],
    }
    report["checks"]["length_breath"] = {
        "over": len(long_breath), "total": total_b,
        "ratio": round(len(long_breath) / total_b * 100, 1) if total_b else 0,
        "hits": long_breath[:10],
    }

    # 5 直播腔
    lh = []
    for ln, s in lines:
        for w in LIVE_WORDS:
            if w in s:
                lh.append({"line": ln, "word": w, "text": s[:40]})
    report["checks"]["live_words"] = {"count": len(lh), "hits": lh}

    # 6 计数位（字面词表；排除元陈述/愿望句）
    cnt, excluded = 0, []
    for ln, s in lines:
        if META.search(s):
            excluded.append({"line": ln, "why": "元陈述", "text": s[:40]})
            continue
        if WISH.search(s):
            excluded.append({"line": ln, "why": "愿望句", "text": s[:40]})
            continue
        for w in COUNT_WORDS:
            if w in s:
                cnt += s.count(w)
    report["checks"]["self_doubt"] = {"count": cnt, "excluded_lines": len(excluded)}

    # 7 一行一句
    multi = []
    for ln, s in lines:
        if len(SENT_END.findall(s)) >= 2:
            multi.append({"line": ln, "text": s[:40]})
    report["checks"]["one_line"] = {"count": len(multi), "hits": multi[:10]}

    # 汇总
    fails = []
    if hits:
        fails.append(f"自称 {len(hits)} 处")
    if ph:
        fails.append(f"禁用标点 {len(ph)} 处")
    if ah:
        fails.append(f"阿拉伯数字 {len(ah)} 处")
    if report["checks"]["length_period"]["ratio"] > 5:
        fails.append(f"句长(句号口径)超限 {report['checks']['length_period']['ratio']}%")
    if lh:
        fails.append(f"直播腔 {len(lh)} 处")
    if multi:
        fails.append(f"一行多句 {len(multi)} 行")
    report["violations"] = len(fails)
    report["fails"] = fails
    report["status"] = "pass" if not fails else "fail"
    return report


def render(r):
    c = r["checks"]
    out = [f"== 扫描：{r['file']}" + (f"（第 {r['ep']} 期）" if r["ep"] else "") + " =="]
    ok = lambda n: "OK" if n == 0 else "NG"
    out.append(f"[1] 自称        {ok(c['self_ref']['count'])} {c['self_ref']['count']} 处")
    for h in c["self_ref"]["hits"][:5]:
        out.append(f"       L{h['line']}: 「{h['word']}」 {h['text']}")
    out.append(f"[2] 禁用标点    {ok(c['punctuation']['count'])} {c['punctuation']['count']} 处")
    for h in c["punctuation"]["hits"][:5]:
        out.append(f"       L{h['line']}: {h['kind']}「{h['char']}」 {h['text']}")
    out.append(f"[3] 阿拉伯数字  {ok(c['arabic']['count'])} {c['arabic']['count']} 处")
    for h in c["arabic"]["hits"][:5]:
        out.append(f"       L{h['line']}: 「{h['char']}」 {h['text']}")
    lp, lb = c["length_period"], c["length_breath"]
    out.append(f"[4] 句长        句号口径 {lp['over']}/{lp['total']}（{lp['ratio']}%，最长 {lp['longest']} 字）")
    out.append(f"                换气口径 {lb['over']}/{lb['total']}（{lb['ratio']}%）")
    out.append(f"[5] 直播腔      {ok(c['live_words']['count'])} {c['live_words']['count']} 处")
    for h in c["live_words"]["hits"][:5]:
        out.append(f"       L{h['line']}: 「{h['word']}」 {h['text']}")
    out.append(f"[6] 计数位      {c['self_doubt']['count']} 次（已排除元陈述/愿望句 {c['self_doubt']['excluded_lines']} 行）")
    out.append(f"[7] 一行一句    {ok(c['one_line']['count'])} {c['one_line']['count']} 行含多个句末标点")
    for h in c["one_line"]["hits"][:3]:
        out.append(f"       L{h['line']}: {h['text']}")
    out.append("")
    out.append(f"结论：{r['status'].upper()}" + ("（" + "；".join(r["fails"]) + "）" if r["fails"] else ""))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="成稿扫描器")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--dir")
    ap.add_argument("--ep")
    ap.add_argument("--json")
    args = ap.parse_args()

    targets = []
    if args.dir:
        targets = sorted(p for p in Path(args.dir).rglob("*.md"))
    elif args.file:
        targets = [Path(args.file)]
    else:
        ap.error("给一个文件，或 --dir 目录")

    reports = []
    for p in targets:
        if not p.exists():
            print(f"找不到：{p}", file=sys.stderr)
            sys.exit(2)
        r = scan(p, args.ep)
        reports.append(r)
        print(render(r))
        print()

    if args.json:
        out = reports[0] if len(reports) == 1 else reports
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写 {args.json}")

    sys.exit(0 if all(r["status"] == "pass" for r in reports) else 1)


if __name__ == "__main__":
    main()
