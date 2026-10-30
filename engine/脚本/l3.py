#!/usr/bin/env python3
"""l3.py —— L3（原始日志层）查询器。只读，给 maker 用。

D10 要求的功能：索引筛选 / 全文检索 / 取原文 / 统计 / 导出 / 校验。
**两层隔离**：她的工具（her-host）不暴露 L3；本脚本不提供任何写操作（除 --out 导出副本）。

用法：
  python 脚本/l3.py list                       # 期目录（读 index.csv）
  python 脚本/l3.py list --since 2026-10-27 --limit 5
  python 脚本/l3.py show 3                     # 第 3 期四件套（成稿/复盘/事件/scan）
  python 脚本/l3.py show 3 --kind 成稿          # 只取一件
  python 脚本/l3.py grep "壳是共用的"            # 全文检索（期 + handoff + 归档）
  python 脚本/l3.py grep "走样" --kind 触动       # 只查「触动.md」（不走智能体）
  python 脚本/l3.py grep "岔路" --kind 项目       # 只查「项目思考.md」
  python 脚本/l3.py grep "稿" --kind handoff --limit 3
  python 脚本/l3.py handoff                    # 会话列表（时间/客户端/open）
  python 脚本/l3.py handoff --open             # 只看没关掉的
  python 脚本/l3.py stats                      # 统计
  python 脚本/l3.py check                      # 校验：索引 vs 文件、缺件、孤儿
  python 脚本/l3.py export --out D:/tmp/l3dump  # 导出副本（唯一会写的操作）
  任意命令加 --json 输出机器可读
"""

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
import sys as _s
_s.path.insert(0, str(ROOT / "脚本"))

L3 = DATA / "记忆" / "L3"
INDEX = L3 / "index.csv"
EP_DIRS = sorted([p for p in L3.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}", p.name)]) if L3.exists() else []
HANDOFF = L3 / "handoff"
ARCHIVE = L3 / "归档"
GRAVE = L3 / "_墓碑-已废.jsonl"  # 2026-10-30：机制已废，不再统计
KINDS = ("成稿", "复盘", "事件", "scan")


def out(obj, as_json):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2))


def read(p):
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


# ---------------- 索引 ----------------
def load_index():
    if not INDEX.exists():
        return []
    with INDEX.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def ep_files(ep, kind=None):
    """第 N 期的文件；kind 为空则四件套全要。"""
    hits = []
    for d in EP_DIRS:
        for p in sorted(d.glob(f"#{ep}-*")):
            k = p.stem.split("-", 1)[1] if "-" in p.stem else ""
            if kind and k != kind:
                continue
            hits.append((k, p))
    return hits



# ---------------- 模糊时间 / 模糊关键词 ----------------
import datetime as _dt

_WEEK = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}


def parse_when(text, today=None):
    """把"上周 / 10 月中 / 昨天 / 最近三天 / 2026-10-20 / 第 2 期"解析成 (start, end)。

    认不出返回 (None, None)——**绝不猜**。
    """
    if not text:
        return None, None
    today = today or _dt.date.today()
    s = text.strip().replace(" ", "")

    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d, d

    m = re.fullmatch(r"(\d{1,2})月(\d{1,2})[日号]?", s)
    if m:
        d = _dt.date(today.year, int(m.group(1)), int(m.group(2)))
        return d, d

    m = re.fullmatch(r"(\d{1,2})月(中|上旬|下旬|底)?", s)
    if m:
        mo = int(m.group(1))
        part = m.group(2) or ""
        if part == "上旬":
            return _dt.date(today.year, mo, 1), _dt.date(today.year, mo, 10)
        if part == "中":
            return _dt.date(today.year, mo, 11), _dt.date(today.year, mo, 20)
        if part in ("下旬", "底"):
            import calendar
            last = calendar.monthrange(today.year, mo)[1]
            return _dt.date(today.year, mo, 21 if part == "下旬" else 21), _dt.date(today.year, mo, last)
        import calendar
        return _dt.date(today.year, mo, 1), _dt.date(today.year, mo, calendar.monthrange(today.year, mo)[1])

    rel = {"今天": 0, "昨天": 1, "前天": 2, "大前天": 3}
    if s in rel:
        d = today - _dt.timedelta(days=rel[s])
        return d, d

    m = re.fullmatch(r"(?:最近|近|过去)(\d+|[一二三四五六七八九十两]+)(?:天|日)", s)
    if m:
        num = m.group(1)
        cn = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        n = int(num) if num.isdigit() else cn.get(num, 1)
        return today - _dt.timedelta(days=n), today

    if s in ("这周", "本周", "这一周"):
        start = today - _dt.timedelta(days=today.isoweekday() - 1)
        return start, start + _dt.timedelta(days=6)
    if s in ("上周", "上一周"):
        start = today - _dt.timedelta(days=today.isoweekday() + 6)
        return start, start + _dt.timedelta(days=6)
    if s in ("这月", "本月", "这个月"):
        import calendar
        return today.replace(day=1), today.replace(day=calendar.monthrange(today.year, today.month)[1])
    if s in ("上月", "上个月"):
        import calendar
        first = (today.replace(day=1) - _dt.timedelta(days=1))
        return first.replace(day=1), first.replace(day=calendar.monthrange(first.year, first.month)[1])

    m = re.fullmatch(r"第(\d+|[一二三四五六七八九十]+)期", s)
    if m:
        ep = m.group(1)
        if not ep.isdigit():
            cn = {c: i for i, c in enumerate("零一二三四五六七八九", 0)}
            ep = str(cn.get(ep, 0) or ep)
        for r in load_index():
            if str(r.get("ep")) == str(ep).zfill(3):
                d = _dt.date.fromisoformat(r["date"])
                return d, d
        return None, None

    return None, None


def in_range(date_str, start, end):
    if not (start and end):
        return True
    d = (date_str or "")[:10]
    if len(d) != 10:
        return False
    return start.isoformat() <= d <= end.isoformat()


def _norm(s):
    """去标点/空白/下划线，转小写。"""
    return re.sub(r"[\s\W_]+", "", s).lower()


def fuzzy_hit(text, keywords):
    """模糊关键词：忽略大小写、标点、空白；全部关键词都要出现（AND）。"""
    norm = _norm(text)
    return all(_norm(k) in norm for k in keywords if _norm(k))

# ---------------- 命令 ----------------
def cmd_list(a):
    rows = load_index()
    if a.since:
        rows = [r for r in rows if (r.get("date") or "") >= a.since]
    if a.when:
        st, en = parse_when(a.when)
        if not st:
            print(f"[没听懂时间]「{a.when}」认不出。支持：上周 / 这周 / 本月 / 10 月中 / 最近三天 / 10月20号 / 2026-10-20 / 第 2 期")
            return
        rows = [r for r in rows if in_range(r.get("date"), st, en)]
    if a.ep:
        rows = [r for r in rows if r.get("ep") == str(a.ep).zfill(3)]
    rows = rows[-a.limit:] if a.limit else rows
    if a.json:
        return out(rows, True)
    if not rows:
        print("（没有记录）")
        return
    print(f"{'期':<5}{'日期':<12}{'主题':<18}{'字':>5}{'句':>4}  扫描    L1")
    print("-" * 66)
    for r in rows:
        print(f"{r.get('ep',''):<5}{r.get('date',''):<12}{(r.get('topic','') or '')[:16]:<18}"
              f"{r.get('chars',''):>5}{r.get('sentences',''):>4}  {r.get('scan_status',''):<6}{r.get('l1_used','') or '—'}")
    print(f"\n共 {len(rows)} 期")


def cmd_show(a):
    ep = str(a.ep).zfill(3)
    files = ep_files(ep, a.kind)
    if not files:
        print(f"没有第 {ep} 期{'（' + a.kind + '）' if a.kind else ''} 的记录")
        return
    for kind, p in files:
        print("=" * 70)
        print(f"# {ep} · {kind} · {p.name}（{p.stat().st_size} 字节）")
        print("=" * 70)
        text = read(p)
        print(text if a.full or len(text) < 4000 else text[:4000] + "\n…（加 --full 看全文）")


def _file_date(p):
    """尽力取文件日期：handoff 文件名 20261027-0815、期目录 #001-* 读 index、其他返回空。"""
    m = re.match(r"(\d{8})-", p.name)
    if m:
        s = m.group(1)
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    m = re.match(r"#(\d+)-", p.name)
    if m:
        for r in load_index():
            if str(r.get("ep")) == m.group(1):
                return r.get("date", "")
    return ""


def _walk_texts(kind=None):
    """(来源, 路径, 文本) 迭代：期 / handoff / 归档 / 触动 / 项目。"""
    if kind in (None, "触动"):
        p = L3 / "触动.md"
        if p.exists():
            yield ("触动", p, read(p))
    if kind in (None, "项目", "项目思考"):
        p = L3 / "项目思考.md"
        if p.exists():
            yield ("项目", p, read(p))
    if kind in (None, "ep", "期"):
        for d in EP_DIRS:
            for p in sorted(d.glob("#*")):
                yield ("期", p, read(p))
    if kind in (None, "handoff"):
        if HANDOFF.exists():
            for p in sorted(HANDOFF.glob("*.md")):
                if p.name.startswith("_"):
                    continue
                yield ("handoff", p, read(p))
    if kind in (None, "archive", "归档"):
        if ARCHIVE.exists():
            for p in sorted(ARCHIVE.glob("*.md")):
                yield ("归档", p, read(p))
    if kind in (None, "card", "任务卡"):
        d = L3 / "任务卡归档"
        if d.exists():
            for p in sorted(d.glob("*.md")):
                yield ("任务卡", p, read(p))
    if kind in (None, "task", "任务"):
        d = L3 / "任务"
        if d.exists():
            for p in sorted(d.glob("*.jsonl")):
                yield ("任务实例", p, read(p))


def _audit(a, hits):
    """审计一行：查了什么、命中几条。不记原话明文（只 hash + 前 20 字）。"""
    import hashlib
    import json as _json
    from datetime import datetime as _now
    rec = {
        "ts": _now.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "q_hash": hashlib.sha256(a.text.encode("utf-8")).hexdigest()[:8],
        "q_head": a.text[:20],
        "status": "found" if hits else "none",   # 2026-10-28：统一状态字段
        "when": a.when or "",
        "kind": a.kind or "all",
        "hits": len(hits),
        "files": sorted({h["file"] for h in hits})[:5],
    }
    with (L3 / "_查询审计.jsonl").open("a", encoding="utf-8") as f:
        f.write(_json.dumps(rec, ensure_ascii=False) + "\n")


def cmd_grep(a):
    kws = [k for k in re.split(r"[\s,，、;；/]+", a.text) if k.strip()]
    st, en = parse_when(a.when) if a.when else (None, None)
    if a.when and not st:
        print(f"[没听懂时间]「{a.when}」认不出。支持：上周 / 本月 / 10 月中 / 最近三天 / 10月20号 / 第 2 期")
        return

    # ① 有 FTS 索引就先让索引圈出候选文件（快），再逐行精确定位（准）
    cand = None
    fts = L3 / "_fts.db"
    if fts.exists() and not getattr(a, "no_fts", False):
        try:
            import sqlite3
            con = sqlite3.connect(fts)
            short = [k for k in kws if len(_norm(k)) < 3]
            if short:
                cond = " AND ".join(["text LIKE ?"] * len(kws))
                rows = con.execute(f"SELECT path FROM content WHERE {cond}", [f"%{k}%" for k in kws]).fetchall()
            else:
                joiner = " OR " if getattr(a, "any", False) else " AND "
                q = joiner.join(f'"{k}"' for k in kws)
                rows = con.execute("SELECT path FROM l3 WHERE l3 MATCH ?", (q,)).fetchall()
            cand = {r[0] for r in rows}
            if not cand:
                cand = None   # 索引里没有 → 可能索引旧了，退回全文扫，别漏检
            con.close()
        except Exception:
            cand = None

    hits = []
    for src, p, text in _walk_texts(a.kind):
        rel = p.relative_to(L3).as_posix()   # 索引里存的是 /，Windows 的 \ 对不上
        if cand is not None and rel not in cand:
            continue
        if st and not in_range(p.stem[:8], st, en) and not in_range(_file_date(p), st, en):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            ok = (any(_norm(k) in _norm(line) for k in kws if _norm(k))
                  if getattr(a, "any", False) else fuzzy_hit(line, kws))
            if ok:
                hits.append({"src": src, "file": rel, "line": i, "text": line.strip()[:200]})
    hits = hits[: a.limit] if a.limit else hits

    if getattr(a, "audit", False):
        _audit(a, hits)
    if a.json:
        return out(hits, True)
    if not hits:
        print(f"没找到「{a.text}」")
        return
    for h in hits:
        print(f"[{h['src']}] {h['file']}:{h['line']}  {h['text']}")
    print(f"\n共 {len(hits)} 处")


def parse_handoff(p):
    text = read(p)
    m = re.search(r"# handoff · (\S+ \S+) · 客户端：(.+?) · 会话：(\S+)", text)
    date = m.group(1) if m else ""
    client = m.group(2) if m else ""
    entries = len(re.findall(r"^## 条目 \d+", text, re.M))
    opens = len(re.findall(r"^open：true", text, re.M))
    return {"session": p.stem, "date": date, "client": client,
            "entries": entries, "open": opens, "bytes": p.stat().st_size}


def cmd_handoff(a):
    rows = [parse_handoff(p) for p in sorted(HANDOFF.glob("*.md")) if not p.name.startswith("_")] \
        if HANDOFF.exists() else []
    if a.since:
        rows = [r for r in rows if r["date"][:10] >= a.since]
    if a.when:
        st, en = parse_when(a.when)
        if not st:
            print(f"[没听懂时间]「{a.when}」认不出。支持：上周 / 本月 / 10 月中 / 最近三天 / 第 2 期")
            return
        rows = [r for r in rows if in_range(r["date"], st, en)]
    if a.open:
        rows = [r for r in rows if r["open"]]
    if a.json:
        return out(rows, True)
    if not rows:
        print("（没有 handoff 记录）")
        return
    print(f"{'会话':<28}{'时间':<18}{'客户端':<8}{'条目':>4}{'open':>5}")
    print("-" * 66)
    for r in rows:
        print(f"{r['session']:<28}{r['date']:<18}{r['client']:<8}{r['entries']:>4}{r['open']:>5}")
    print(f"\n共 {len(rows)} 次出差")


def cmd_stats(a):
    rows = load_index()
    ep_count = len({p.stem.split("-")[0] for d in EP_DIRS for p in d.glob("#*")})
    chars = sum(int(r.get("chars") or 0) for r in rows)
    passed = sum(1 for r in rows if (r.get("scan_status") or "") == "pass")
    hf = [p for p in HANDOFF.glob("*.md")] if HANDOFF.exists() else []
    hf = [p for p in hf if not p.name.startswith("_")]
    grave = [l for l in read(GRAVE).splitlines() if l.strip()] if GRAVE.exists() else []
    data = {
        "期数": ep_count,
        "索引记录": len(rows),
        "总字数": chars,
        "扫描通过": f"{passed}/{len(rows)}",
        "handoff 会话": len(hf),
        "墓碑": len(grave),
        "L3 目录": [d.name for d in EP_DIRS],
        "文件总数": sum(1 for _ in L3.rglob("*") if _.is_file()) if L3.exists() else 0,
    }
    if a.json:
        return out(data, True)
    for k, v in data.items():
        print(f"{k:<12}{v}")


def check_handoff(problems):
    """handoff 一致性：每个文件能解析、_状态.md 会话数对得上。"""
    import re as _re
    if not HANDOFF.exists():
        return
    files = [p for p in sorted(HANDOFF.glob("20*.md"))]
    bad = []
    for p in files:
        t = read(p)
        if not t.startswith("# handoff ·"):
            bad.append(f"handoff 头部格式不对：{p.name}")
        if not _re.search(r"^## 条目 \d+", t, _re.M):
            bad.append(f"handoff 没有条目：{p.name}")
        for need in ("谁做的", "做了什么"):
            if f"{need}：" not in t:
                bad.append(f"{p.name} 缺「{need}：」")
    st = HANDOFF / "_状态.md"
    if st.exists():
        txt = read(st)
        # 表里一个会话可能占多行（每条目一行），按去重 id 数
        listed = len({m.group(0) for m in _re.finditer(r"20\d{6}-[^#|\s]+", txt)})
        if listed and listed != len(files):
            # 派生表可能落后于新写入：先自动刷新，再判
            try:
                import handoff as _hf
                _hf.status_table()
                txt = read(st)
                listed = len({m.group(0) for m in _re.finditer(r"20\d{6}-[^#|\s]+", txt)})
            except Exception:
                pass
            if listed != len(files):
                bad.append(f"_状态.md 列了 {listed} 个会话，磁盘上有 {len(files)} 个（跑 handoff.py status）")
    problems.extend(bad)


def check_archive(problems):
    """归档一致性：条数/ids 与任务看板 retired 对齐；sha256 与记录一致。"""
    import hashlib
    import json
    arc_md = ARCHIVE / "任务看板-已作废.md"
    arc_js = ARCHIVE / "_归档.json"
    if not arc_md.exists() or not arc_js.exists():
        return
    try:
        meta = json.loads(arc_js.read_text(encoding="utf-8"))
    except Exception:
        problems.append("_归档.json 解析失败")
        return
    dig = hashlib.sha256(arc_md.read_bytes()).hexdigest()
    if meta.get("sha256") and meta["sha256"] != dig:
        problems.append("归档 sha256 与记录不一致（渲染或导入会按两套数据走）——跑 workbench.py render")
    desk = DATA / "入口" / "任务看板.json"
    try:
        d = json.loads(desk.read_text(encoding="utf-8"))
        retired = sorted(x["id"] for x in d.get("decisions", []) if x.get("status") != "active")
        if sorted(meta.get("ids") or []) != retired:
            problems.append(f"归档 ids 与任务看板 retired 不一致：归档 {len(meta.get('ids') or [])} 条、台账 {len(retired)} 条")
    except Exception:
        pass


def cmd_check(a):
    rows = load_index()
    problems = []
    seen = set()
    for r in rows:
        ep = r.get("ep", "")
        for k, col in (("成稿", "draft"), ("复盘", "review"), ("事件", "event"), ("scan", "scan")):
            rel = r.get(col) or ""
            if not rel:
                problems.append(f"第 {ep} 期索引缺「{k}」路径")
                continue
            p = L3 / rel
            if not p.exists():
                problems.append(f"第 {ep} 期索引指向的文件不存在：{rel}")
            else:
                seen.add(p.resolve())
    # 孤儿：文件在，索引里没有
    for d in EP_DIRS:
        for p in d.glob("#*"):
            if p.resolve() not in seen:
                problems.append(f"孤儿文件（索引里没有）：{p.relative_to(L3)}")

    check_handoff(problems)
    check_archive(problems)

    fts = L3 / "_fts.db"
    if fts.exists():
        import datetime as _d
        age = (_d.datetime.now() - _d.datetime.fromtimestamp(fts.stat().st_mtime)).total_seconds()
        if age > 86400:
            problems.append("FTS 索引超过 1 天没更新（跑 python 脚本/l3_index.py build）")
    data = {"索引记录": len(rows), "问题数": len(problems), "问题": problems,
            "fts": fts.exists()}
    if a.json:
        return out(data, True)
    print(f"索引记录 {len(rows)} 条｜问题 {len(problems)} 个")
    for p in problems:
        print("  -", p)
    if not problems:
        print("[OK] 全部对得上（无缺件、无孤儿）")
    else:
        sys.exit(1)   # 有问题就非零退出，收尾链路据此报警


def cmd_export(a):
    dst = Path(a.out)
    if dst.exists() and any(dst.iterdir()):
        print(f"[X] 目标非空，先清掉：{dst}")
        sys.exit(2)
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for kind in (a.kind,) if a.kind else (None,):
        for src, p, _ in _walk_texts(kind):
            rel = p.relative_to(L3)
            t = dst / rel
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, t)
            n += 1
    print(f"[OK] 已导出 {n} 个文件 → {dst}")


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="L3 查询器（只读）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list"); p.add_argument("--ep"); p.add_argument("--since")
    p.add_argument("--when", help="模糊时间：上周/10 月中/最近三天/2026-10-20/第 2 期")
    p.add_argument("--limit", type=int)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show"); p.add_argument("ep"); p.add_argument("--kind", choices=KINDS)
    p.add_argument("--full", action="store_true"); p.set_defaults(fn=cmd_show)

    p = sub.add_parser("grep"); p.add_argument("text", help="关键词，空格分隔=全部要命中")
    p.add_argument("--kind", choices=["ep", "handoff", "archive", "触动", "项目"])
    p.add_argument("--when", help="模糊时间（同 list）")
    p.add_argument("--no-fts", action="store_true", help="不用 FTS 索引，直接扫文件")
    p.add_argument("--audit", action="store_true", help="写一行查询审计（她调用时必带）")
    p.add_argument("--any", action="store_true", help="关键词任一命中即可（默认全部命中）")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_grep)

    p = sub.add_parser("handoff"); p.add_argument("--since")
    p.add_argument("--when", help="模糊时间（同 list）")
    p.add_argument("--open", action="store_true")
    p.set_defaults(fn=cmd_handoff)

    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    sub.add_parser("check").set_defaults(fn=cmd_check)

    p = sub.add_parser("export"); p.add_argument("--out", required=True)
    p.add_argument("--kind", choices=["ep", "handoff", "archive", "触动", "项目"]); p.set_defaults(fn=cmd_export)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
