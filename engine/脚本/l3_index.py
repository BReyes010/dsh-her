#!/usr/bin/env python3
"""l3_index：给 L3 建 SQLite FTS5 索引（永久记忆的检索层）。

为什么现在上：B 档十年约 2 万条 / 15 MB，grep 还够用；但结构一旦定死，
以后再加"触动的话/项目思考"再换检索，迁移成本比现在大。**热路径提前标准备好。**

设计：
  · 独立库 `记忆/L3/_fts.db`（不是真源，删了能重建）
  · 两张表：
      files(path, kind, date, mtime, size)         ← 增量依据
      content(path, text)                          ← 原文副本（LIKE 兜底用）
      l3 USING fts5(path, text, tokenize='trigram')← 最短 3 字查询走这里
  · 中文 <3 字（"走样"）trigram 不命中 → 自动退到 LIKE
  · 增量：按 mtime+size 只重建变化的文件

用法：
  python 脚本/l3_index.py build [--force]      # 建/增量更新
  python 脚本/l3_index.py stats                # 看索引状态
  python 脚本/l3_index.py search "壳 账"        # 直接用索引查（调试用）
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from paths import rel

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
L3 = DATA / "记忆" / "L3"
DB = L3 / "_fts.db"

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS files(
         path TEXT PRIMARY KEY, kind TEXT, date TEXT, mtime REAL, size INTEGER)""",
    """CREATE TABLE IF NOT EXISTS content(path TEXT PRIMARY KEY, text TEXT)""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS l3 USING fts5(
         path UNINDEXED, text, tokenize='trigram')""",
]

SKIP = {"_fts.db", "_查询审计.jsonl"}


def _norm(s):
    return re.sub(r"[\s\W_]+", "", s).lower()


def walk():
    """产出 (相对路径, kind, date, 文本)。"""
    for p in sorted(L3.rglob("*")):
        if not p.is_file() or p.name in SKIP or p.suffix in (".db", ".json") and p.name.startswith("_") and p.name != "_归档.json":
            continue
        rel = p.relative_to(L3).as_posix()
        # 派生/模板不索引：_状态.md、_模板.md、_promoted.json 等
        if "/" not in rel and p.name.startswith("_"):
            continue
        if p.name.startswith("_") and "_closings" not in rel:
            continue
        if rel.startswith("_") and not rel.startswith("_"):
            continue
        if p.name == "触动.md":
            kind = "触动"
        elif p.name == "项目思考.md":
            kind = "项目"
        elif p.parent.name.isdigit():
            kind = "期"
        elif "handoff" in rel:
            kind = "handoff"
        elif rel.startswith("归档/"):
            kind = "归档"
        else:
            kind = "其他"
        m = re.match(r"(\d{8})-", p.name)
        date = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else ""
        if not date:
            m2 = re.match(r"#(\d+)-", p.name)
            if m2:
                date = _ep_date(m2.group(1))
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        yield rel, kind, date, p.stat().st_mtime, p.stat().st_size, text


def _ep_date(ep):
    idx = L3 / "index.csv"
    if not idx.exists():
        return ""
    import csv
    with idx.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("ep")) == ep:
                return r.get("date", "")
    return ""


def connect():
    con = sqlite3.connect(DB)
    for s in SCHEMA:
        con.execute(s)
    return con


def cmd_build(a):
    con = connect()
    known = {r[0]: (r[1], r[2]) for r in con.execute("SELECT path, mtime, size FROM files")}
    added = updated = skipped = 0
    # 循环变量叫 p，不能叫 rel——会把模块里的 rel() 遮住（2026-10-29 修）
    for p, kind, date, mtime, size, text in walk():
        old = known.get(p)
        if old and abs(old[0] - mtime) < 1e-6 and old[1] == size and not a.force:
            skipped += 1
            continue
        con.execute("DELETE FROM l3 WHERE path=?", (p,))
        con.execute("DELETE FROM content WHERE path=?", (p,))
        con.execute("INSERT INTO l3(path, text) VALUES(?,?)", (p, text))
        con.execute("INSERT INTO content(path, text) VALUES(?,?)", (p, text))
        con.execute("""INSERT INTO files(path, kind, date, mtime, size) VALUES(?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET kind=excluded.kind, date=excluded.date,
                       mtime=excluded.mtime, size=excluded.size""",
                    (p, kind, date, mtime, size))
        (updated := updated + 1) if old else (added := added + 1)
    # 删掉已不存在的文件
    alive = {r[0] for r in con.execute("SELECT path FROM files")}
    onDisk = {p for p, *_ in walk()}
    for gone in alive - onDisk:
        con.execute("DELETE FROM l3 WHERE path=?", (gone,))
        con.execute("DELETE FROM content WHERE path=?", (gone,))
        con.execute("DELETE FROM files WHERE path=?", (gone,))
        print(f"  - 移除已删文件：{gone}")
    con.commit()
    total = con.execute("SELECT count(*) FROM files").fetchone()[0]
    con.close()
    print(f"[OK] 索引 {rel(DB)}｜新增 {added}｜更新 {updated}｜未变 {skipped}｜共 {total} 个文件")


def cmd_stats(a):
    if not DB.exists():
        print("还没建索引：python 脚本/l3_index.py build")
        return
    con = connect()
    print(f"库：{rel(DB)}（{DB.stat().st_size/1024:.0f} KB）")
    for kind, n in con.execute("SELECT kind, count(*) FROM files GROUP BY kind"):
        print(f"  {kind:<8}{n} 个文件")
    print(f"  最长文件：", con.execute("SELECT path, length(text) n FROM content ORDER BY n DESC LIMIT 1").fetchone())
    con.close()


def cmd_search(a):
    if not DB.exists():
        print("还没建索引")
        return
    con = connect()
    kws = [k for k in re.split(r"[\s,，、;；/]+", a.text) if k.strip()]
    short = [k for k in kws if len(_norm(k)) < 3]
    if short:
        cond = " AND ".join(["text LIKE ?"] * len(kws))
        rows = con.execute(f"SELECT path FROM content WHERE {cond}",
                           [f"%{k}%" for k in kws]).fetchall()
    else:
        q = " AND ".join(f'"{k}"' for k in kws)
        rows = con.execute("SELECT path FROM l3 WHERE l3 MATCH ?", (q,)).fetchall()
    print(f"命中 {len(rows)} 个文件（{'LIKE 兜底' if short else 'FTS5 trigram'}）")
    for (p,) in rows[:10]:
        print("  -", p)
    con.close()


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="L3 检索索引（可重建）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build"); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_build)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    p = sub.add_parser("search"); p.add_argument("text"); p.set_defaults(fn=cmd_search)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
