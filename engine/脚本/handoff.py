#!/usr/bin/env python3
"""出差记忆（handoff）—— 按 `入口/出差记忆-方案.md` 实现。

规则摘要：
  · 一次**收尾** = 一个文件 `记忆/L3/handoff/<会话号>-<客户端>.md`，内含 1–3 条主题条目
  · L3 原文**写那一刻就落盘**，永久、永不注入
  · T1（常驻视图）= **上一次会话的全部条目（≤3）** + **所有 open 条目（≤3，独立于替换）**
  · open 只有被 `closes: <id>` 指到时才转 closed
  · 超过 3 天的条目注入时标 `[这条有点旧]`（只标，不删）

用法：
  python handoff.py write --client 千问 --stdin         # 从标准输入读一段 markdown
  python handoff.py write --client 千问 --file a.md
  python handoff.py list-t1                             # 打印 T1（给注入用）
  python handoff.py list                                # 列所有会话文件
  python handoff.py close --id 20261027-0915#1 --client DSH --why "那段改顺了"
  python handoff.py flags                               # 报旗：open 超限 / 反省过密
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from paths import rel

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
from paths import paths as _paths
DIR = _paths()["handoff"]
STALE_DAYS = 3
MAX_T1 = 3
MAX_OPEN = 3

HEAD = re.compile(r"^#\s*handoff\s*·\s*(?P<stamp>[\d\-: ]+)\s*·\s*客户端：(?P<client>\S+)"
                  r"(?:\s*·\s*会话：(?P<session>[\w\-]+))?")
ITEM = re.compile(r"^##\s*条目\s*(\d+)", re.M)
KV = re.compile(r"^(谁做的|做了什么|主题|起点|卡点|走过的岔路|试过没成的|取舍|哪里还没想明白|原话|open|closes|留下)\s*[：:]\s*(.*)$")


def refresh_pack():
    """handoff 一落盘就重生成启动包（保证"最近一次记忆"是最新的）。"""
    try:
        status_table()
    except Exception:
        pass
    script = ROOT / "脚本" / "make_mode_pack.py"
    if not script.exists():
        return
    try:
        subprocess.run([sys.executable, str(script)], capture_output=True, timeout=120)
        status_table()  # 状态表跟着刷新（否则 _状态.md 会停在旧时间）
    except Exception:
        pass


def ensure_dir():
    DIR.mkdir(parents=True, exist_ok=True)


def parse_file(path):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    meta = {"stamp": "", "client": "", "session": path.stem}
    if lines:
        m = HEAD.match(lines[0].strip())
        if m:
            meta.update({k: (v or "") for k, v in m.groupdict().items()})
        meta["session"] = path.stem  # 一律以文件名为准，避免老文件会话号与文件名不一致
    parts = ITEM.split(text)
    items = []
    # parts: [head, "1", body1, "2", body2, ...]
    for i in range(1, len(parts) - 1, 2):
        n, body = parts[i], parts[i + 1]
        rec = {"id": f"{meta['session']}#{n}", "session": meta["session"],
               "client": meta["client"], "stamp": meta["stamp"], "no": n}
        multi = ("走过的岔路", "试过没成的")
        for line in body.splitlines():
            m = KV.match(line.strip())
            if not m:
                continue
            k, v = m.group(1), m.group(2).strip()
            if k in multi:
                rec.setdefault(k, [])
                if isinstance(rec[k], str):
                    rec[k] = [rec[k]]
                if v and v not in ("—", "-"):
                    rec[k].append(v)
            else:
                rec[k] = v
        items.append(rec)
    return {"path": path, "meta": meta, "items": items}


def all_sessions():
    ensure_dir()
    # 按修改时间排序（文件名含中文时，字典序会把"测试"排到 DSH 之后）
    files = sorted(DIR.glob("*.md"), key=lambda p: (p.stat().st_mtime, p.stem))
    return [parse_file(f) for f in files if not f.name.startswith("_")]


def all_closings():
    d = DIR / "_closings"
    if not d.exists():
        return []
    return [parse_file(f) for f in sorted(d.glob("*.md"))]


def closed_ids(sessions):
    out = set()
    for s in list(sessions) + all_closings():
        for it in s["items"]:
            c = it.get("closes", "").strip()
            if c and c not in ("—", "-", ""):
                out.update(x.strip() for x in c.split(","))
    return out


def is_open(it):
    v = it.get("open", "").strip().lower()
    return v in ("true", "是", "1", "yes", "y")


def closing_map():
    """{被收口的 id: (原因, 时间)}"""
    out = {}
    for s in all_closings():
        for it in s["items"]:
            for tid in (it.get("closes") or "").split(","):
                tid = tid.strip()
                if tid and tid not in ("—", "-"):
                    out[tid] = (it.get("原话", "").strip() or it.get("做了什么", ""), s["meta"].get("stamp", ""))
    return out


def recent_closings(limit=2):
    """最近收口的几条（给 T1 用：让她知道哪些事已经不用再想）。"""
    m = closing_map()
    items = []
    for sid, (reason, stamp) in m.items():
        items.append((stamp, sid, reason))
    items.sort(reverse=True)
    return items[:limit]


def status_table():
    """生成 `_状态.md`：所有条目的 open / 已收口 / 原因，一个地方看全。"""
    sessions = all_sessions()
    closed = closing_map()
    lines = ["# handoff 状态（自动生成）", "",
             "> `open：true` 在会话文件里可能已被收口；**以本表为准**。", "",
             "| id | 做了什么 | 文件状态 | 实际状态 | 收口原因 |", "|---|---|---|---|---|"]
    for s in sessions:
        for it in s["items"]:
            iid = it["id"]
            is_c = iid in closed
            st = "已收口" if is_c else ("open" if is_open(it) else "closed")
            lines.append(f"| {iid} | {it.get('做了什么', '')[:28]} | open={it.get('open', '—')} | {st} | {closed.get(iid, ('', ''))[0][:24] if is_c else '—'} |")
    p = DIR / "_状态.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p, len(sessions)


def _as_list(v):
    if not v:
        return []
    if isinstance(v, list):
        return v
    v = str(v).strip()
    return [] if v in ("—", "-") else [v]


def fmt_item(it, stale=False):
    """T1 呈现：尽量摆事实（起点/岔路/试过的），让她少推理。"""
    lines = []
    tag = " [这条有点旧]" if stale else ""
    who = (it.get("谁做的") or "").strip()
    mark = "" if who in ("她", "", "—", "-") else f"（{who}做的）"
    lines.append(f"- {it.get('stamp', '')[:16]} · {it.get('client', '?')}{tag}{mark}")

    def add(label, key):
        v = (it.get(key) or "").strip()
        if v and v not in ("—", "-", "无", "没有", "None", "none"):
            lines.append(f"  {label}：{v}")

    add("做的", "做了什么")
    add("起点", "起点")
    add("卡在", "卡点")
    for i, v in enumerate(_as_list(it.get("走过的岔路")), 1):
        lines.append(f"  岔路{i}：{v}")
    for i, v in enumerate(_as_list(it.get("试过没成的")), 1):
        lines.append(f"  走过死路{i}：{v}")
    add("取舍", "取舍")
    add("还没想明白", "哪里还没想明白")
    add("原话", "原话")
    add("留下", "留下")
    return "\n".join(lines)


def t1_block(limit_session=MAX_T1, limit_open=MAX_OPEN):
    """T1：open 优先，再来上一次会话的条目。"""
    sessions = all_sessions()
    if not sessions:
        return ""
    closed = closed_ids(sessions)
    now = datetime.now()
    opens, latest = [], []
    last = sessions[-1]
    for s in sessions:
        for it in s["items"]:
            if it["id"] in closed:
                continue
            if is_open(it):
                opens.append(it)
            if s["path"] == last["path"]:
                latest.append(it)
    opens = opens[-limit_open:]
    ordinary = [it for it in latest if it not in opens][:limit_session]
    if not opens and not ordinary:
        return ""

    def stale(it):
        try:
            ts = datetime.strptime(it["stamp"][:16], "%Y-%m-%d %H:%M")
            return (now - ts).days >= STALE_DAYS
        except Exception:
            return False

    out = ["## 你不在的时候（出差记忆）"]
    if opens:
        out.append("**还没解决的事**")
        out += [fmt_item(it, stale(it)) for it in opens]
    if ordinary:
        out.append("**上一次出差**")
        out += [fmt_item(it, stale(it)) for it in ordinary]
    rc = recent_closings()
    if rc:
        out.append("**已经收口的（不用再想）**")
        for stamp, sid, reason in rc:
            out.append(f"- {sid}：{reason or '（无说明）'}")
    return "\n".join(out)


def write_session(client, body, session=None):
    """写一次收尾。**绝不覆盖已有会话**：重名就加 -2/-3 后缀。"""
    ensure_dir()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    sid = session or datetime.now().strftime("%Y%m%d-%H%M")
    path = DIR / f"{sid}-{client}.md"
    n = 2
    while path.exists():
        path = DIR / f"{sid}-{client}-{n}.md"
        n += 1
    header = f"# handoff · {stamp} · 客户端：{client} · 会话：{path.stem}\n\n"
    path.write_text(header + body.strip() + "\n", encoding="utf-8")
    refresh_pack()
    return path


def write_close(client, target_id, why):
    """收口记录单独放 `_closings/`，不参与"上一次出差"的选取，也不覆盖别的东西。"""
    d = DIR / "_closings"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    path = d / f"close-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.md"
    body = "\n".join([
        f"# handoff · {stamp} · 客户端：{client} · 会话：close",
        "",
        "## 条目 1",
        "谁做的：助手",
        f"做了什么：收口 {target_id}",
        f"原话：{why}",
        f"closes：{target_id}",
    ])
    path.write_text(body + "\n", encoding="utf-8")
    refresh_pack()
    return path


def flags():
    sessions = all_sessions()
    closed = closed_ids(sessions)
    opens = [it for s in sessions for it in s["items"] if is_open(it) and it["id"] not in closed]
    out = []
    if len(opens) > MAX_OPEN:
        out.append(f"[旗] 未解决的事有 {len(opens)} 条（上限 {MAX_OPEN}）")

    # 防表演 / 防敷衍：看最近 3 次会话（每次取会话里的"还没想明白"是否都填了）
    recent_items = [it for s in sessions[-3:] for it in s["items"]]
    filled = [it for it in recent_items
              if (it.get("哪里还没想明白") or "").strip() not in ("", "—", "-", "无")]
    if len(recent_items) >= 5 and len(filled) >= len(recent_items) - 1:
        out.append(f"[旗] 最近 {len(recent_items)} 条里有 {len(filled)} 条都在写「哪里还没想明白」——留意是不是在表演反省")
    if len(recent_items) >= 7 and not filled:
        out.append(f"[旗] 最近 {len(recent_items)} 条全是「—」（没卡点）——只报不逼，确认不是敷衍")

    # D25 补丁：只有"助手做的"、没有她的判断 → 提示她补一条“我同意了/我否了”
    if sessions:
        last = sessions[-1]
        hers = [it for it in last["items"] if (it.get("谁做的") or "").strip() in ("她", "")]
        others = [it for it in last["items"]
                  if (it.get("谁做的") or "").strip() not in ("她", "", "—", "-")]
        if others and not hers:
            out.append("[旗] 最近一次收尾只有「助手做的」条目，没有她的判断——建议补一条 `谁做的：她`，只写「我同意了／我否了 + 依据」")
    return out, len(opens), len(sessions)


def promote(ep=None):
    """T1 到期 → 把旧会话的条目提炼成 L2 `过程` 条目（必须带 主题：）。

    - 只处理"不是最新一次"的会话；
    - 幂等：已提炼过的 id 记在 `_promoted.json`；
    - 没有 `主题：` 的条目跳过（留在 L3）。
    """
    import json
    sessions = all_sessions()
    if len(sessions) < 2:
        return [], []
    mark = DIR / "_promoted.json"
    done = set(json.loads(mark.read_text(encoding="utf-8"))) if mark.exists() else set()
    added, skipped = [], []
    for s in sessions[:-1]:
        for it in s["items"]:
            if it["id"] in done:
                continue
            who = (it.get("谁做的") or "").strip()
            if who and who not in ("她", "—", "-"):
                skipped.append(f"{it['id']}（不是她做的）")
                continue
            topic = (it.get("主题") or "").strip()
            if not topic or topic in ("—", "-"):
                skipped.append(it["id"])
                continue
            body = "｜".join(x for x in [
                it.get("做了什么", ""),
                f"卡点：{it.get('卡点')}" if it.get("卡点") not in (None, "—", "-") else "",
                f"岔路：{it.get('走过的岔路')}" if it.get("走过的岔路") else "",
                f"还没想明白：{it.get('哪里还没想明白')}" if it.get("哪里还没想明白") not in (None, "—", "-") else "",
            ] if x)
            cmd = [sys.executable, str(ROOT / "脚本" / "memory_db.py"), "add",
                   "--title", f"过程-{topic}-{it['id']}", "--category", "过程",
                   "--body", body[:280], "--keys", topic, "--scene", "日常",
                   "--source", "reflection", "--note", f"由 handoff {it['id']} 自动提炼"]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if r.returncode == 0:
                done.add(it["id"])
                added.append(it["id"])
            else:
                skipped.append(f"{it['id']}（{r.stderr.strip()[:40]}）")
    mark.write_text(json.dumps(sorted(done), ensure_ascii=False, indent=1), encoding="utf-8")
    return added, skipped


def rotate(days=3, backup_days=30, dry_run=False):
    """活跃区只留「最近 days 天」+「所有还有 open 的会话」；其余移进 归档/YYYY-MM/。

    超过 backup_days 的月份目录压成 `归档/YYYY-MM.zip`（带 sha，记账在 `归档/_归档.json`）。
    只动不带 `_` 前缀的会话文件——`_待写入-*` 这类草稿不碰（归{角色名}处理）。

    返回 (moved, zipped, kept)。
    """
    import hashlib
    import json as _json
    import shutil
    import zipfile

    ARCH = DIR / "归档"
    ARCH.mkdir(exist_ok=True)
    now = datetime.now()
    cutoff = now - timedelta(days=days)

    sessions = all_sessions()                      # 已排除 `_` 前缀
    keep, move = [], []
    for s in sessions:
        items = s["items"]
        has_open = any(is_open(it) and it.get("id") not in closing_map() for it in items)
        fresh = datetime.fromtimestamp(s["path"].stat().st_mtime) >= cutoff
        (keep if (fresh or has_open) else move).append(s)

    if dry_run:
        print(f"[dry-run] 活跃 {len(keep)}｜移档 {len(move)}｜超过 {backup_days} 天的月份会压缩")
        for s in move:
            print(f"   → 归档/{datetime.fromtimestamp(s['path'].stat().st_mtime):%Y-%m}/{s['path'].name}")
        return len(move), 0, len(keep)

    for s in move:
        stamp = datetime.fromtimestamp(s["path"].stat().st_mtime)
        sub = ARCH / f"{stamp:%Y-%m}"
        sub.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s["path"]), str(sub / s["path"].name))

    # 压缩过期月份
    meta_path = ARCH / "_归档.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    months = meta.get("months") or {}
    zipped = 0
    for sub in sorted(ARCH.iterdir()):
        if not sub.is_dir() or not re.fullmatch(r"\d{4}-\d{2}", sub.name):
            continue
        # 按**月份本身**判断新旧（目录 mtime 是"刚建"的时间，不能用）
        try:
            yy, mm = (int(x) for x in sub.name.split("-"))
        except Exception:
            continue
        month_end = (datetime(yy + (1 if mm == 12 else 0), 1 if mm == 12 else mm + 1, 1)
                     - timedelta(days=1))
        if (now - month_end).days < backup_days:
            continue
        zpath = ARCH / f"{sub.name}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(sub.glob("*.md")):
                z.write(f, f.name)
        months[sub.name] = {
            "files": len(list(sub.glob("*.md"))),
            "zip": zpath.name,
            "sha256": hashlib.sha256(zpath.read_bytes()).hexdigest(),
            "zipped_at": now.strftime("%Y-%m-%d %H:%M"),
        }
        shutil.rmtree(sub)
        zipped += 1
    meta["months"] = months
    meta["updated"] = now.strftime("%Y-%m-%d %H:%M")
    meta["keep_days"] = days
    meta_path.write_text(_json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    status_table()
    return len(move), zipped, len(keep)


def main():
    ap = argparse.ArgumentParser(description="出差记忆（handoff）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("write")
    p.add_argument("--client", required=True)
    p.add_argument("--file")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--clipboard", action="store_true", help="从系统剪贴板读取（外部客户端收尾后最省事）")
    p.add_argument("--session")

    sub.add_parser("list-t1")
    sub.add_parser("list")

    p = sub.add_parser("close")
    p.add_argument("--id", required=True)
    p.add_argument("--client", required=True)
    p.add_argument("--why", default="已解决")

    sub.add_parser("flags")
    sub.add_parser("status")
    p = sub.add_parser("rotate")
    p.add_argument("--days", type=int, default=STALE_DAYS, help="活跃期天数，默认 3")
    p.add_argument("--backup-days", type=int, default=30, help="归档月包保留天数，默认 30")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("promote")
    p.add_argument("--ep")

    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if a.cmd == "write":
        if getattr(a, "clipboard", False):
            r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            body = r.stdout or ""
        elif a.file:
            body = Path(a.file).read_text(encoding="utf-8")
        else:
            body = sys.stdin.read()
        if not body.strip():
            sys.exit(2)
        p = write_session(a.client, body, a.session)
        print(f"已写入 {rel(p)}")
    elif a.cmd == "list-t1":
        print(t1_block())
    elif a.cmd == "list":
        for s in all_sessions():
            print(f"{s['path'].stem}  条目 {len(s['items'])}  ← {s['path'].name}")
    elif a.cmd == "close":
        body = "\n".join([
            "## 条目 1",
            "谁做的：她",
            f"做了什么：收口 {a.id}",
            "卡点：—",
            "取舍：—",
            "哪里还没想明白：—",
            "open：false",
            f"closes：{a.id}",
            f"留下：{a.why}",
        ])
        p = write_close(a.client, a.id, a.why)
        print(f"已收口 {a.id}（写入 {rel(p)}）")
    elif a.cmd == "promote":
        added, skipped = promote(a.ep)
        print(f"提炼 {len(added)} 条 → L2 过程库" + (f"；跳过 {len(skipped)} 条（无主题或已存在）" if skipped else ""))
        if added:
            print("  " + "、".join(added))
    elif a.cmd == "status":
        p, n = status_table()
        print(f"已生成 {rel(p)}（{n} 个会话）")
    elif a.cmd == "rotate":
        moved, zipped, kept = rotate(a.days, a.backup_days, a.dry_run)
        if a.dry_run:
            print(f"[dry-run] 会移档 {moved} 个会话，活跃保留 {kept} 个")
        else:
            print(f"[OK] 移档 {moved} 个 · 压缩 {zipped} 个月 · 活跃保留 {kept} 个")
    elif a.cmd == "flags":
        out, nopen, nsess = flags()
        for line in out or ["（无旗）"]:
            print(line)
        print(f"open={nopen} sessions={nsess}")


if __name__ == "__main__":
    main()
