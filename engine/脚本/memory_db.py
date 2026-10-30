#!/usr/bin/env python3
"""记忆库（SQLite 真源）—— 按 `记忆/L2-规范.md` 实现。

真源：`记忆/memory.db`；`记忆/记忆.md`、`记忆/场景投影.md` 是自动导出的视图。
- import  **保元字段**（按 num upsert，不清表）
- export  **哈希守卫**（视图被手改则拒绝覆盖，除非 --force）
- approve / reject / cite  —— 签字、拒收、记录引用期（都留痕到 l2_changelog）

用法：
  python memory_db.py init
  python memory_db.py import [--md 路径]
  python memory_db.py export [--force]
  python memory_db.py list [--scene 工作] [--pending] [--all]
  python memory_db.py add --title … --category … --body … [--keys a,b] [--scene 工作] [--constant] [--source maker]
  python memory_db.py approve --id 101 [--reason "可以"]
  python memory_db.py reject  --id 101 --reason "太笼统"
  python memory_db.py cite    --id 3 --ep 001
  python memory_db.py scenes
  python memory_db.py source --id 3
  python memory_db.py changelog [--limit 20]

退出码：0 正常；2 库/文件/参数问题；3 违规（重复标题、待确认满、视图被手改）。
"""

import argparse
import hashlib
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
from paths import paths as _paths
_P = _paths()
MEM_DIR = _P["db"].parent
DB = _P["db"]
MD = _P["memory_md"]
SCENE_MD = _P["scene_md"]

MAX_PENDING = 5
SEP = "=" * 64
HEAD = re.compile(r"^第\s*(\d+)\s*条｜分类：(.+)$")
FIELD = re.compile(r"^(关键词|触发区域)：(.*)$")
TITLE = re.compile(r"^【(.+)】$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  num INTEGER, layer TEXT, category TEXT, title TEXT,
  keywords TEXT, insert_order INTEGER,
  enabled INTEGER DEFAULT 1, pending INTEGER DEFAULT 0, constant INTEGER DEFAULT 0,
  scene TEXT DEFAULT '通用', source TEXT DEFAULT 'import', date TEXT,
  status TEXT DEFAULT 'active', last_cited_ep TEXT,
  raw TEXT
);
CREATE TABLE IF NOT EXISTS projections(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scene TEXT UNIQUE, title TEXT, inject TEXT, strict TEXT, relaxed TEXT,
  enabled INTEGER DEFAULT 1, note TEXT
);
CREATE TABLE IF NOT EXISTS l2_changelog(
  ts TEXT, action TEXT, num INTEGER, detail TEXT
);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def today():
    return date.today().isoformat()


def connect(create=False):
    if not DB.exists() and not create:
        sys.exit("库不存在，先跑 init")
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        ensure_unique_index(con)
        con.commit()
    except sqlite3.Error as e:
        print(f"[warn] 唯一索引建不上：{e}", file=sys.stderr)
    return con


def unique_num(con, num):
    """编号必须唯一命中，否则拒绝执行——宁可报错，也不能签错对象。"""
    row = con.execute("SELECT COUNT(*) c FROM entries WHERE num=?", (num,)).fetchone()
    if row["c"] != 1:
        print(f"编号 {num} 命中 {row['c']} 条（应为 1 条），拒绝执行以免签错对象", file=sys.stderr)
        sys.exit(3)


def ensure_unique_index(con):
    """给 entries.num 建唯一索引；有重号时跳过并告警（不硬上）。"""
    dup = con.execute("SELECT COUNT(*) c FROM (SELECT num FROM entries GROUP BY num HAVING COUNT(*)>1)").fetchone()["c"]
    if dup:
        print(f"[warn] 有 {dup} 个重复编号，暂不建唯一索引；先跑 --check-num", file=sys.stderr)
        return False
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_entries_num ON entries(num)")
    return True


def log(con, action, num, detail=""):
    con.execute("INSERT INTO l2_changelog(ts,action,num,detail) VALUES(?,?,?,?)",
                (today(), action, num, detail))


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------- 解析 / 导出 ----------

def parse_blocks(text):
    parts = re.split(r"^={20,}$", text, flags=re.M)
    preamble = parts[0]
    chunks = [p.strip("\n") for p in parts[1:] if p.strip()]
    return preamble, chunks


def parse_chunk(raw):
    rec = dict(num=None, layer=None, category=None, title=None, keywords="",
               insert_order=None, enabled=1, pending=0, constant=0,
               scene="通用", raw=raw)
    for line in raw.splitlines():
        s = line.strip()
        m = HEAD.match(s)
        if m:
            rec["num"] = int(m.group(1))
            rec["category"] = m.group(2).strip()
            continue
        m = TITLE.match(s)
        if m:
            rec["title"] = m.group(1).strip()
            continue
        m = FIELD.match(s)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if k == "关键词":
                rec["keywords"] = v.replace("_or_", "").strip()
            else:
                for seg in v.split("｜"):
                    if "：" not in seg:
                        continue
                    kk, vv = [x.strip() for x in seg.split("：", 1)]
                    if kk == "启用":
                        rec["enabled"] = 1 if vv == "True" else 0
                    elif kk == "插入顺序":
                        rec["insert_order"] = int(vv) if vv.isdigit() else None
                    elif kk == "常驻":
                        rec["constant"] = 1 if vv == "True" else 0
                    elif kk == "待确认":
                        rec["pending"] = 1 if vv == "True" else 0
                    elif kk == "场景":
                        rec["scene"] = vv
    if rec["category"] == "主题索引":
        rec["layer"] = "主题索引"
    elif rec["pending"]:
        rec["layer"] = "待确认"
    elif rec["constant"]:
        rec["layer"] = "常驻层"
    else:
        rec["layer"] = "关键词层"
    return rec


def cmd_import(a):
    src = Path(a.md) if a.md else MD
    if not src.exists():
        sys.exit(2)
    text = src.read_text(encoding="utf-8")
    preamble, chunks = parse_blocks(text)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    old = {r["num"]: dict(r) for r in con.execute("SELECT * FROM entries")}
    seen, added, updated = set(), 0, 0
    for raw in chunks:
        r = parse_chunk(raw)
        num = r["num"]
        if num is None:
            continue
        seen.add(num)
        if num in old:
            o = old[num]
            # 保留元字段（L2 规范 §8 硬线 1）
            con.execute(
                "UPDATE entries SET layer=?,category=?,title=?,keywords=?,insert_order=?,"
                "enabled=?,pending=?,constant=?,scene=?,raw=? WHERE num=?",
                (r["layer"], r["category"], r["title"], r["keywords"], r["insert_order"],
                 r["enabled"], r["pending"], r["constant"], r["scene"], r["raw"], num))
            updated += 1
        else:
            con.execute(
                "INSERT INTO entries(num,layer,category,title,keywords,insert_order,enabled,"
                "pending,constant,scene,source,date,status,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (num, r["layer"], r["category"], r["title"], r["keywords"], r["insert_order"],
                 r["enabled"], r["pending"], r["constant"], r["scene"], "import", today(), "active", r["raw"]))
            added += 1
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('preamble',?)", (preamble,))
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('md_sha',?)", (sha(text),))
    log(con, "import", None, f"新增 {added} / 更新 {updated} / 未在视图中的旧条保留 {len(set(old)-seen)}")
    con.commit()
    print(f"导入完成：新增 {added}，更新 {updated}，保留 {len(set(old)-seen)}（元字段未丢）")
    con.close()


def pending_section(con):
    """按库生成〈待确认区〉正文（2026-10-29 修：以前写死在 preamble，库变了视图不动）。"""
    rows = con.execute("SELECT num,title,category FROM entries WHERE pending=1 ORDER BY num").fetchall()
    if not rows:
        return "（暂无）"
    lines = [f"共 {len(rows)} 条待签字：", ""]
    for r in rows:
        lines.append(f"- 第 {r['num']} 条｜{r['category'] or '未分类'}｜{r['title'] or '（无标题）'}（等签字）")
    return "\n".join(lines)


def splice_pending(text, section):
    """把〈待确认区〉的正文换成按库生成的那段（全文扫描，只改"（暂无）/条待签字"那种）。"""
    import re as _re
    pat = _re.compile(r"(^## 待确认区\s*$)(.*?)(?=^={10,}|^## |\Z)", _re.S | _re.M)
    out = []
    last = 0
    for m in pat.finditer(text):
        body = m.group(2)
        if ("（暂无）" not in body) and ("条待签字" not in body):
            continue
        keep = [l for l in body.splitlines()
                if l.strip().startswith("自动沉淀") or l.strip().startswith("**maker**")]
        keep_txt = ("\n\n".join(keep) + "\n\n") if keep else "\n"
        out.append(text[last:m.start(2)])
        out.append(keep_txt + section + "\n\n")
        last = m.end(2)
    if not out:
        return text
    out.append(text[last:])
    return "".join(out)

def cmd_export(a):
    con = connect()
    preamble = (con.execute("SELECT v FROM meta WHERE k='preamble'").fetchone() or [""])[0]
    rows = con.execute("SELECT raw FROM entries ORDER BY id").fetchall()
    out = [preamble.rstrip("\n")]
    for row in rows:
        out.append(SEP)
        out.append((row["raw"] or "").strip("\n"))
    text = splice_pending("\n".join(out) + "\n", pending_section(con))

    old_sha = (con.execute("SELECT v FROM meta WHERE k='md_sha'").fetchone() or [None])[0]
    if MD.exists() and old_sha:
        cur = sha(MD.read_text(encoding="utf-8"))
        if cur != old_sha and not a.force:
            print("视图被手改过，拒绝覆盖（L2 规范 §8 硬线 2）。", file=sys.stderr)
            print("  确认要丢弃手改：export --force", file=sys.stderr)
            sys.exit(3)
    MD.write_text(text, encoding="utf-8")
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('md_sha',?)", (sha(text),))
    log(con, "export", None, f"{len(rows)} 条")
    write_scene_view(con)
    con.commit()
    print(f"已导出：{MD.name}（{len(rows)} 条）+ {SCENE_MD.name}")
    con.close()


def write_scene_view(con):
    rows = con.execute("SELECT * FROM projections ORDER BY id").fetchall()
    lines = ["# 场景投影清单（自动生成，勿手改）", "",
             "> 真源在 `记忆/memory.db` 的 `projections` 表。改这里无效，改库后重新 export。", "",
             "| 场景 | 注入块 | 加强的底线 | 放宽的底线 | 状态 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['scene']} | {r['inject'] or '—'} | {r['strict'] or '—'} | {r['relaxed'] or '—'} | {'启用' if r['enabled'] else '停用'} |")
        if r["note"]:
            lines += ["", f"**{r['scene']}**：{r['note']}"]
    SCENE_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_list(a):
    con = connect()
    q = "SELECT num,layer,category,title,enabled,pending,scene,status,last_cited_ep FROM entries WHERE 1=1"
    p = []
    if a.pending:
        q += " AND pending=1"
    elif not a.all:
        q += " AND pending=0"
    if a.scene:
        q += " AND (scene=? OR scene='通用')"
        p.append(a.scene)
    rows = con.execute(q + " ORDER BY num", p).fetchall()
    print(f"{'#':>4} | {'层':<5} | {'场景':<3} | {'启':<2} | {'待':<2} | {'状态':<9} | {'引用':<5} | 标题")
    print("-" * 96)
    for r in rows:
        print(f"{r['num']:>4} | {(r['layer'] or '')[:4]:<5} | {r['scene']:<3} | "
              f"{'是' if r['enabled'] else '否':<2} | {'是' if r['pending'] else '否':<2} | "
              f"{(r['status'] or ''):<9} | {(r['last_cited_ep'] or '—'):<5} | {r['title']}")
    print(f"\n共 {len(rows)} 条")
    con.close()


def _policy():
    try:
        import policy as _p
        return _p
    except Exception:
        return None


def _auto_ok(category):
    """→ (能不能自动转正, 理由)。只有 simple 模式才可能为 True。"""
    p = _policy()
    if p is None:
        return False, "没有 policy 模块：按严格处理"
    try:
        return p.auto_approve_category(category)
    except Exception as e:
        return False, f"策略判断失败：{e}"


def cmd_add(a):
    con = connect()
    if con.execute("SELECT 1 FROM entries WHERE title=?", (a.title,)).fetchone():
        print(f"标题重复，拒收：{a.title}", file=sys.stderr)
        sys.exit(3)
    auto_ok, auto_why = _auto_ok(a.category)
    pend = con.execute("SELECT COUNT(*) c FROM entries WHERE pending=1").fetchone()["c"]
    if not auto_ok and pend >= MAX_PENDING:
        print(f"待确认区已满（{pend}/{MAX_PENDING}），先处理再沉淀（L2 规范 §3）。", file=sys.stderr)
        sys.exit(3)
    # 取号必须看全表：只看待确认区会导致签完字后号码被复用
    nxt = con.execute("SELECT COALESCE(MAX(num),100)+1 n FROM entries").fetchone()["n"] or 101
    nxt = max(nxt, 101)
    keys = (a.keys or "").strip()
    scene = a.scene or "通用"
    order = 1 + con.execute("SELECT COUNT(*) c FROM entries WHERE constant=1").fetchone()["c"] if a.constant else 1000 + nxt
    chunk = "\n".join([
        f"第 {nxt} 条｜分类：{a.category}",
        f"关键词：_or_{keys}" if not a.constant else "关键词：_or_",
        f"触发区域：启用：False｜插入顺序：{order}｜"
        f"常驻：{'True' if a.constant else 'False'}｜场景：{scene}｜待确认：{'False' if auto_ok else 'True'}",
        f"【{a.title}】",
        "",
        a.body.strip(),
        "",
        f"- 来源：{a.source}",
        f"- 待 maker 确认的点：（{a.note or '待补'}）",
    ])
    con.execute(
        "INSERT INTO entries(num,layer,category,title,keywords,insert_order,enabled,pending,"
        "constant,scene,source,date,status,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (nxt, "关键词层" if auto_ok else "待确认", a.category, a.title, keys, order,
         1 if auto_ok else 0, 0 if auto_ok else 1,
         1 if a.constant else 0, scene, a.source, today(), "active", chunk))
    log(con, "auto-approve" if auto_ok else "add", nxt,
        f"{a.title}｜{a.category}｜{auto_why if auto_ok else '待确认'}")
    con.commit()
    if auto_ok:
        print(f"已自动转正：第 {nxt} 条《{a.title}》（{auto_why}）；跑 export 更新记忆视图")
    else:
        print(f"已入待确认区：第 {nxt} 条《{a.title}》（待确认 {pend+1}/{MAX_PENDING}）｜{auto_why}；跑 export 才会出现在视图")
    con.close()


def refresh_raw_header(con, num):
    """签字/停用后重写 raw 里那行「触发区域」，否则视图和控制台永远是待确认（2026-10-29 修）"""
    r = con.execute("SELECT * FROM entries WHERE num=?", (num,)).fetchone()
    if not r:
        return
    raw = r["raw"] or ""
    line = (f"触发区域：启用：{'True' if r['enabled'] else 'False'}｜插入顺序：{r['insert_order']}｜"
            f"常驻：{'True' if r['constant'] else 'False'}｜场景：{r['scene']}｜"
            f"待确认：{'True' if r['pending'] else 'False'}")
    if re.search(r"^触发区域：.*$", raw, flags=re.M):
        raw = re.sub(r"^触发区域：.*$", line, raw, count=1, flags=re.M)
    else:
        raw = line + "\n" + raw
    con.execute("UPDATE entries SET raw=? WHERE num=?", (raw, num))


def cmd_repair_raw(a):
    """全量比对 raw 里的「启用/待确认」与列值；--apply 才改（默认只报）。"""
    con = connect()
    rows = con.execute("SELECT num,pending,enabled,constant,insert_order,scene,raw FROM entries").fetchall()
    bad = []
    for r in rows:
        raw = r["raw"] or ""
        m1 = re.search(r"启用：\s*(True|False)", raw)
        m2 = re.search(r"待确认：\s*(True|False)", raw)
        if m1 and ((m1.group(1) == "True") != bool(r["enabled"])):
            bad.append(r["num"])
        elif m2 and ((m2.group(1) == "True") != bool(r["pending"])):
            bad.append(r["num"])
    if not bad:
        print(f"raw 与列值一致（{len(rows)} 条），不用修。")
        con.close()
        return
    print(f"raw 与列值不一致：{len(bad)} 条 → {bad}")
    if not a.apply:
        print("只报不改。要修加 --apply（会重写这些条目的触发区域行，然后 export）。")
        con.close()
        return
    for n in bad:
        refresh_raw_header(con, n)
        log(con, "repair-raw", n, "raw 触发区域按列值重写")
    con.commit()
    print(f"已修 {len(bad)} 条；跑 export 让视图跟上")
    con.close()


def cmd_supersede(a):
    """用第 --new 条取代第 --old 条：旧的停用并记指针，新的保持生效。"""
    con = connect()
    for n in (a.old, a.new):
        if not con.execute("SELECT 1 FROM entries WHERE num=?", (n,)).fetchone():
            print(f"没有第 {n} 条", file=sys.stderr)
            sys.exit(2)
    if a.old == a.new:
        print("old 与 new 不能相同", file=sys.stderr)
        sys.exit(2)
    if con.execute("SELECT status FROM entries WHERE num=?", (a.old,)).fetchone()["status"] == "superseded":
        print(f"第 {a.old} 条已被取代过", file=sys.stderr)
        sys.exit(3)
    note = f"被第 {a.new} 条取代（{today()}）：{a.why or '—'}"
    con.execute("UPDATE entries SET enabled=0, status='superseded', raw=raw||? WHERE num=?",
                ("\n\n" + note + "\n", a.old))
    refresh_raw_header(con, a.old)
    log(con, "supersede", a.old, f"→ #{a.new}：{a.why or ''}")
    con.commit()
    print(f"第 {a.old} 条已停用（status=superseded），指针指向第 {a.new} 条；跑 export 更新视图")
    con.close()

def cmd_approve(a):
    con = connect()
    unique_num(con, a.id)
    r = con.execute("SELECT * FROM entries WHERE num=?", (a.id,)).fetchone()
    if not r:
        sys.exit(2)
    if not r["pending"]:
        print(f"第 {a.id} 条不在待确认区（pending=0），无需签字", file=sys.stderr)
        sys.exit(3)
    row = con.execute("SELECT constant, num FROM entries WHERE num=?", (a.id,)).fetchone()
    layer = "常驻层" if row["constant"] else ("主题索引" if row["num"] == 11 else "关键词层")
    con.execute("UPDATE entries SET pending=0, enabled=1, status='active', layer=? WHERE num=?",
                (layer, a.id))
    refresh_raw_header(con, a.id)
    log(con, "approve", a.id, a.reason or "签字生效")
    con.commit()
    print(f"第 {a.id} 条已生效（enabled=1, pending=0, status=active）；记得 export")
    con.close()


def cmd_reject(a):
    con = connect()
    unique_num(con, a.id)
    r = con.execute("SELECT * FROM entries WHERE num=?", (a.id,)).fetchone()
    if not r:
        sys.exit(2)
    if not r["pending"]:
        print(f"第 {a.id} 条不在待确认区", file=sys.stderr)
        sys.exit(3)
    raw = (r["raw"] or "") + f"\n- 拒收理由（{today()}）：{a.reason}"
    con.execute("UPDATE entries SET pending=0, enabled=0, status='rejected', raw=? WHERE num=?",
                (raw, a.id))
    log(con, "reject", a.id, a.reason)
    con.commit()
    print(f"第 {a.id} 条已拒收（status=rejected，保留可查、不注入）")
    con.close()


def cmd_cite(a):
    con = connect()
    unique_num(con, a.id)
    con.execute("UPDATE entries SET last_cited_ep=? WHERE num=?", (a.ep.zfill(3), a.id))
    log(con, "cite", a.id, f"#{a.ep}")
    con.commit()
    print(f"第 {a.id} 条：最近引用期 = #{a.ep.zfill(3)}")
    con.close()


def cmd_scenes(_):
    con = connect()
    for r in con.execute("SELECT * FROM projections ORDER BY id"):
        print(f"[{r['scene']}] {r['title']} — {'启用' if r['enabled'] else '停用'}")
        print(f"  注入：{r['inject']}")
        if r["strict"]:
            print(f"  加强：{r['strict']}")
        if r["relaxed"]:
            print(f"  放宽：{r['relaxed']}")
    con.close()


def cmd_source(a):
    con = connect()
    unique_num(con, a.id)
    r = con.execute("SELECT raw FROM entries WHERE num=?", (a.id,)).fetchone()
    print(r["raw"] if r else f"没有第 {a.id} 条")
    con.close()


def cmd_tick(a):
    """按 L2 规范 §4 / 出差记忆方案：过程类条目连续 8 期未命中 → 归档（enabled=0）。"""
    con = connect()
    ep = int(a.ep)
    rows = con.execute(
        "SELECT num,title,last_cited_ep FROM entries WHERE category=? AND status=? AND enabled=1",
        ("过程", "active")).fetchall()
    archived = []
    for r in rows:
        last = r["last_cited_ep"]
        if not last:
            continue
        try:
            gap = ep - int(last)
        except ValueError:
            continue
        if gap >= 8:
            con.execute("UPDATE entries SET status=?, enabled=0 WHERE num=?", ("archived", r["num"]))
            log(con, "archive", r["num"], f"连续 {gap} 期未命中（>={8}）")
            archived.append((r["num"], r["title"], gap))
    con.commit()
    if archived:
        for num, title, gap in archived:
            print(f"归档 第 {num} 条「{title}」：连续 {gap} 期未命中")
    else:
        print(f"第 {ep} 期：无需归档（过程类条目 {len(rows)} 条）")
    con.close()


def cmd_flags(a):
    """报旗：待确认积压 / 过程类条目数。"""
    con = connect()
    pend = con.execute("SELECT COUNT(*) c FROM entries WHERE pending=1").fetchone()["c"]
    proc = con.execute("SELECT COUNT(*) c FROM entries WHERE category=? AND enabled=1", ("过程",)).fetchone()["c"]
    if pend >= 5:
        print(f"[旗] 待确认区已满（{pend}/5），先处理再沉淀")
    if proc > 6:
        print(f"[旗] 过程类条目 {proc} 条（上限 6），考虑合并")
    if pend < 5 and proc <= 6:
        print("（无旗）")
    print(f"待确认 {pend}/5 · 过程条目 {proc}/6")
    con.close()


def cmd_check_num(a):
    con = connect()
    dups = list(con.execute("SELECT num, COUNT(*) c FROM entries GROUP BY num HAVING c>1"))
    if not dups:
        print("[OK] 没有重复编号")
    else:
        print("[X] 有重复编号：")
        for r in dups:
            print(f"  num={r['num']}  出现 {r['c']} 次")
    con.close()


def cmd_changelog(a):
    con = connect()
    for r in con.execute("SELECT * FROM l2_changelog ORDER BY rowid DESC LIMIT ?", (a.limit,)):
        print(f"{r['ts']}  {r['action']:<8} {str(r['num'] or '—'):<4} {r['detail'] or ''}")
    con.close()


def main():
    ap = argparse.ArgumentParser(description="记忆库（L2）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=lambda a: (connect(create=True), print(f"已建 {DB}")))
    p = sub.add_parser("import"); p.add_argument("--md"); p.set_defaults(fn=cmd_import)
    p = sub.add_parser("export"); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("list"); p.add_argument("--scene"); p.add_argument("--pending", action="store_true"); p.add_argument("--all", action="store_true"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("add")
    p.add_argument("--title", required=True); p.add_argument("--category", default="未分类")
    p.add_argument("--body", required=True); p.add_argument("--keys")
    p.add_argument("--scene", default="通用"); p.add_argument("--constant", action="store_true")
    p.add_argument("--source", default="reflection"); p.add_argument("--note")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("approve"); p.add_argument("--id", type=int, required=True); p.add_argument("--reason"); p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("reject"); p.add_argument("--id", type=int, required=True); p.add_argument("--reason", required=True); p.set_defaults(fn=cmd_reject)
    p = sub.add_parser("cite"); p.add_argument("--id", type=int, required=True); p.add_argument("--ep", required=True); p.set_defaults(fn=cmd_cite)
    sub.add_parser("scenes").set_defaults(fn=cmd_scenes)
    p = sub.add_parser("source"); p.add_argument("--id", type=int, required=True); p.set_defaults(fn=cmd_source)
    p = sub.add_parser("repair-raw"); p.add_argument("--apply", action="store_true"); p.set_defaults(fn=cmd_repair_raw)
    p = sub.add_parser("supersede"); p.add_argument("--old", type=int, required=True); p.add_argument("--new", type=int, required=True); p.add_argument("--why"); p.set_defaults(fn=cmd_supersede)
    sub.add_parser("check-num").set_defaults(fn=cmd_check_num)
    p = sub.add_parser("tick"); p.add_argument("--ep", required=True); p.set_defaults(fn=cmd_tick)
    sub.add_parser("flags").set_defaults(fn=cmd_flags)
    p = sub.add_parser("changelog"); p.add_argument("--limit", type=int, default=20); p.set_defaults(fn=cmd_changelog)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
