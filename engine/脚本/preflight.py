#!/usr/bin/env python3
"""preflight：开场自检。用户说「继续」或开新对话时跑一次。

它回答两个问题：
  ① **上次收尾写进去了吗**——任务看板更新了，但 L3 没有对应的 handoff？
  ② **这一次要加载的东西对吗**——卡/模式解析、L0 指纹、记忆条数、待确认、索引新鲜度、预算。

用法：
  python 脚本/preflight.py                  # 默认卡 + 日常模式
  python 脚本/preflight.py --card dafeiyu --mode 完整
  python 脚本/preflight.py --json

退出码：0 一切正常；1 有需要你注意的（不阻塞）；2 有硬错误（别继续）。
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
sys.path.insert(0, str(ROOT / "脚本"))

L3 = DATA / "记忆" / "L3"
HANDOFF = L3 / "handoff"
DESK = DATA / "入口" / "任务看板.md"
FTS = L3 / "_fts.db"
warn, err, info = [], [], []


def sh(args):
    p = subprocess.run([sys.executable, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def check_load(card, mode):
    """① 这次要加载什么、对不对。"""
    desk = {"日常": "now", "工作": "brief", "完整": "full"}[mode]
    rc, out, e = sh([str(ROOT / "入口" / "her-host.py"), "--print", mode, "--desk", desk])
    if rc != 0:
        err.append(f"取包失败：{(e or out)[:120]}")
        return {}
    han = sum(1 for c in out if "\u4e00" <= c <= "\u9fff")
    # 2026-10-30：三包取消，映射必须一一对应，不能再拿错文件比
    #   日常 → 出差包.txt（本体）
    #   工作 → 预设-写稿.txt（本体+口播+素材）
    #   完整 → 没有落地包（三包取消了），只报引擎字数，不跟文件比
    PACK_OF = {"日常": "出差包.txt", "工作": "预设-写稿.txt", "完整": None}
    name = PACK_OF.get(mode)
    pack = (DATA / "启动包" / name) if name else None
    if pack is not None and pack.exists():
        pt = pack.read_text(encoding="utf-8", errors="replace")
        phan = sum(1 for c in pt if "\u4e00" <= c <= "\u9fff")
        # 预设是"卡清单"拼出来的，跟 --print 的工作视角不同源；
        # 同源核对：拿 load_cmd 现算一遍，和包文件比
        if mode == "工作":
            rc2, out2, _ = sh([str(ROOT / "脚本" / "load_cmd.py"),
                               f"载入{card} 口播"])
            h2 = sum(1 for c in out2 if "\u4e00" <= c <= "\u9fff") if rc2 == 0 else -1
            info.append(f"注入包：{card} / 工作｜预设-写稿 {phan} 汉字｜"
                        f"现算（载入{card} 口播）{h2} 汉字"
                        f"（差 {phan - h2} = 预设带素材 + 包头印章；现算只带父卡+子卡）")
        else:
            info.append(f"注入包：{card} / {mode}｜引擎 {han} 汉字｜"
                        f"包文件 {name} {phan} 汉字（差 {phan - han} = 包头+包尾）")
    elif name is None:
        info.append(f"注入包：{card} / 完整｜引擎 {han} 汉字（完整模式**没有落地包**：三包已取消）")
    else:
        info.append(f"注入包：{card} / {mode}｜引擎 {han} 汉字（没有 {name}，跑 make_mode_pack.py）")
    # 按模式分支断言（原来那条"日常不许有任务看板"在完整模式下会误报）
    if "# 人格卡" not in out:
        err.append("注入包里没有人格卡内核")
    if mode == "日常":
        if "## 任务看板（上一次会话" in out:
            err.append("日常模式里出现了整段任务看板（不该有）")
        if "口播投影" in out:
            err.append("日常模式里出现了口播投影（不该有）")
    elif mode == "工作":
        if "口播投影" not in out:
            err.append("工作模式里没有口播投影")
        if "## 任务看板（上一次会话" in out:
            err.append("工作模式里出现了整段任务看板（该只带「这一期」）")
    else:
        for need in ("口播投影", "L0′ 技术前缀", "## 任务看板（上一次会话"):
            if need not in out:
                err.append(f"完整模式缺少「{need}」")
    if mode in ("日常", "工作"):
        has_block = "## 这一趟我是投影" in out
        has_line = "投影：只读、不记事" in out or "这一趟我是投影" in out
        if not (has_block or has_line):
            err.append(f"{mode}模式缺少投影自述")
    return {"han": han}


def check_l0():
    rc, out, e = sh([str(ROOT / "脚本" / "l0_guard.py")])
    if rc != 0:
        err.append("L0 守卫不通过：" + (out or e).splitlines()[0])
    else:
        m = re.search(r"版本：正本 (\S+)", out)
        info.append(f"L0：{m.group(1) if m else '?'}，指纹一致")


def check_memory():
    import sqlite3
    import paths
    p = paths.paths()
    con = sqlite3.connect(p["db"])
    const = con.execute("SELECT COUNT(*) FROM entries WHERE constant=1 AND enabled=1 AND pending=0").fetchone()[0]
    pend = con.execute("SELECT COUNT(*) FROM entries WHERE pending=1").fetchone()[0]
    con.close()
    info.append(f"L2：常驻 {const} 条，待确认 {pend} 条")
    if const > 4:
        warn.append(f"常驻层 {const} 条，超过上限 4 条")
    if pend:
        warn.append(f"待确认区有 {pend} 条没签字（她可能提了东西在等）")


def check_close():
    """② 上次收尾写了吗：任务看板更新时间 vs 最后一次 handoff。"""
    if not DESK.exists():
        warn.append("任务看板不存在")
        return
    files = sorted(HANDOFF.glob("20*.md"), key=lambda p: p.stat().st_mtime) if HANDOFF.exists() else []
    today = datetime.now().date()
    desk_date = None
    m = re.search(r"最后更新：(\d{4}-\d{2}-\d{2})", DESK.read_text(encoding="utf-8", errors="replace"))
    if m:
        desk_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    if not files:
        if desk_date == today:
            warn.append("任务看板今天更新过，但一条 handoff 都没有——上次收尾可能没记出差记忆")
        return
    last = files[-1]
    if not any(datetime.fromtimestamp(f.stat().st_mtime).date() == today for f in files):
        warn.append("今天还没有 handoff 记录——这次会话结束前记得写")
    if desk_date and desk_date > datetime.fromtimestamp(last.stat().st_mtime).date():
        gap = (desk_date - datetime.fromtimestamp(last.stat().st_mtime).date()).days
        warn.append(f"任务看板比最后一次 handoff 新 {gap} 天——上次收尾可能只更新了台账，没写 handoff")
    info.append(f"L3：最近一次 handoff {last.name}")
    rc, out, _ = sh([str(ROOT / "脚本" / "handoff.py"), "flags"])
    if out and "open=" in out:
        info.append("T1：" + out.splitlines()[-1])


def check_pack_memory(mode):
    """包里的记忆是不是跟盘上同版。"""
    meta_f = DATA / "启动包" / "_meta.json"
    if not meta_f.exists():
        warn.append("启动包缺 _meta.json（跑一次 make_mode_pack.py）")
        return
    try:
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
    except Exception:
        warn.append("_meta.json 解析失败")
        return
    import paths
    db = paths.paths()["db"]
    if abs(db.stat().st_mtime - float(meta.get("mem_mtime", 0))) > 1:
        warn.append(f"启动包里的记忆比盘上旧（包 {meta.get('built_at')} 生成，之后记忆库又变过）"
                    "——重跑 make_mode_pack.py")
    else:
        info.append(f"启动包与记忆同版（{meta.get('entries')} 条，{meta.get('built_at')}）")


def check_pending_close():
    f = DATA / "记忆" / "L3" / "handoff" / "_待补.json"
    if not f.exists():
        return
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        warn.append("有「待补」标记但读不出来")
        return
    warn.append(f"有一轮没收尾：{rec.get('ts')}｜{rec.get('mode')}｜{rec.get('line')}"
                "（补写 handoff 后自动清掉）")



SESSION_LOG = ROOT / "记忆" / "L3" / "_会话.jsonl"


def check_trip_memory():
    """出差记忆（handoff）：提醒「收尾时要把这趟记下来」。

    maker 2026-10-28 订正：不是"出差包"，是**出差记忆**——
    外部客户端那一趟（出差）结束时要写 handoff；自检只负责把"该写而没写"指出来。
    """
    import datetime as _dt
    d = ROOT / "记忆" / "L3" / "handoff"
    files = sorted(d.glob("20*.md"), key=lambda p: p.stat().st_mtime) if d.exists() else []
    rc, out, _ = sh([str(ROOT / "脚本" / "handoff.py"), "flags"])
    open_n = 0
    for line in (out or "").splitlines():
        if "open=" in line:
            try:
                open_n = int(line.split("open=")[1].split()[0])
            except Exception:
                pass
    if not files:
        warn.append("出差记忆：一条都还没有——这趟收尾时要写 handoff")
        return
    latest = files[-1]
    age = (_dt.datetime.now() - _dt.datetime.fromtimestamp(latest.stat().st_mtime)).days
    if age >= 2:
        warn.append(f"出差记忆：最近一條是 {age} 天前（{latest.name}）——收尾时记得补这趟的")
    else:
        info.append(f"出差记忆：最近 {latest.name}（{age} 天前），open={open_n}")


TODO_LOG = ROOT / "入口" / "_筛.jsonl"
TODO_SEC = "## 待办·推送分级"
MARKS = ("不可逆", "花钱", "内核", "发布", "第三方", "签发", "删")


def _todo_items():
    f = ROOT / "待办与待决.md"
    if not f.exists():
        return []
    txt = f.read_text(encoding="utf-8")
    i = txt.find(TODO_SEC)
    if i < 0:
        return []
    body = txt[i + len(TODO_SEC):]
    nxt = body.find("\n## ")
    if nxt > 0:
        body = body[:nxt]
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("- [ ]"):
            continue
        item = line[5:].strip()
        import re as _re
        m = _re.search(r"（([^）]*)）", item)
        mark = m.group(1) if m else ""
        out.append((item, mark))
    return out


def check_open_items():
    """挂着 open 的事要点名——不然"等第二次"永远没人认。"""
    try:
        sys.path.insert(0, str(ROOT / "脚本"))
        import handoff as _hf
        sessions = _hf.all_sessions()
    except Exception:
        return
    rows = []
    for s in sessions:
        for it in s["items"]:
            if not _hf.is_open(it):
                continue
            if it.get("id") in _hf.closing_map():
                continue
            rows.append((s["meta"].get("stamp", ""), it))
    if not rows:
        return
    rows.sort(key=lambda x: x[0], reverse=True)
    info.append(f"挂着 open：{len(rows)} 条")
    for stamp, it in rows[:3]:
        did = (it.get("做了什么") or "").strip()
        tail = "（等第二次）" if any(k in (did + (it.get("哪里还没想明白") or "")) for k in ("第二次", "单次", "L1")) else ""
        warn.append(f"open｜{stamp[:10]}｜{did[:30]}{tail}")


def _age_days(stamp):
    """'2026-10-28' 或 '2026-10-28 17:46' → 过了几天（取不到返回 None）。"""
    import re as _re
    from datetime import date, datetime as _dt
    t = str(stamp)
    m = _re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if not m:
        m = _re.search(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", t)   # 20261028 这种
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None
    return (date.today() - d).days


def check_pending_all():
    """把所有"等他签字/处理"的东西汇总成一段——积压才不会烂在角落里。"""
    import json as _json
    import sqlite3 as _sq
    from datetime import date as _date

    rows = []          # (标签, 成立?, 文本)

    # ① L2 待确认
    try:
        con = _sq.connect(DATA / "记忆" / "memory.db")
        pend = con.execute("SELECT date FROM entries WHERE pending=1").fetchall()
        con.close()
        if pend:
            ages = [a for a in (_age_days(d[0]) for d in pend) if a is not None]
            old = max(ages) if ages else 0
            rows.append(("L2 待确认", True, f"{len(pend)} 条｜最老 {old} 天", old > 7))
    except Exception:
        pass

    # ② L1 候选
    try:
        l1 = None
        sys.path.insert(0, str(ROOT / "脚本"))
        import paths as _p
        l1 = _p.paths().get("l1")
        if l1 and Path(l1).exists():
            con = _sq.connect(l1)
            cand = con.execute("SELECT created FROM l1 WHERE state='candidate'").fetchall()
            con.close()
            if cand:
                ages = [a for a in (_age_days(c[0]) for c in cand) if a is not None]
                old = max(ages) if ages else 0
                rows.append(("L1 候选", True, f"{len(cand)} 条｜最老 {old} 天", old > 7))
    except Exception:
        pass

    # ③ 提案待签
    try:
        pf = DATA / "提案" / "_提案.json"
        if pf.exists():
            props = _json.loads(pf.read_text(encoding="utf-8")).get("props", [])
            pend = [x for x in props if x.get("state") == "pending"]
            if pend:
                ages = [a for a in (_age_days(x.get("created")) for x in pend) if a is not None]
                old = max(ages) if ages else 0
                ids = "、".join(x["id"] for x in pend[:4])
                rows.append(("提案待签", True, f"{len(pend)} 条（{ids}）｜最老 {old} 天", old > 7))
    except Exception:
        pass

    # ④ 待批准队列
    try:
        q = DATA / "入口" / "_批准.json"
        if q.exists():
            items = _json.loads(q.read_text(encoding="utf-8")).get("items", [])
            pend = [x for x in items if x.get("state") == "pending"]
            if pend:
                ages = [a for a in (_age_days(x.get("asked_at")) for x in pend) if a is not None]
                old = max(ages) if ages else 0
                rows.append(("待批准", True, f"{len(pend)} 条｜最老 {old} 天", old > 7))
    except Exception:
        pass

    # ⑤ 待写入草稿（她当时没权限，先存成文件）
    try:
        hd = DATA / "记忆" / "L3" / "handoff"
        drafts = sorted(hd.glob("_待写入-*")) if hd.exists() else []
        if drafts:
            ages = [a for a in (_age_days(x.name) for x in drafts) if a is not None]
            old = max(ages) if ages else 0
            rows.append(("待写入草稿", True, f"{len(drafts)} 个｜最老 {old} 天", old > 7))
    except Exception:
        pass

    # ⑥ 自动转正（简易模式免核实的条目）——不算"等你处理"，但要能抽检
    try:
        con = _sq.connect(DATA / "记忆" / "memory.db")
        from datetime import date as _d, timedelta as _td
        since = (_d.today() - _td(days=7)).isoformat()
        n_auto = con.execute(
            "SELECT COUNT(*) FROM l2_changelog WHERE action='auto-approve' AND ts>=?",
            (since,)).fetchone()[0]
        con.close()
        if n_auto:
            info.append(f"自动转正：最近 7 天 {n_auto} 条（简易模式免核实）"
                        "——想抽检：python 脚本/memory_db.py list --all，错的用 supersede 回滚")
    except Exception:
        pass

    if not rows:
        info.append("等你处理：0 件（没有待确认 / 候选 / 提案 / 待批准 / 草稿）")
        return

    total = sum(int(t.split(" ")[0]) for _, _, t, _ in rows)
    info.append(f"等你处理：{total} 件")
    for label, _, text, red in rows:
        line = f"{label}：{text}"
        if red:
            warn.append("积压｜" + line + "（超过 7 天没人处理）")
        else:
            info.append(line)


def check_l1():
    """L1：候选几条、最老多久（40 天没认领该归档）、活跃几行。"""
    import sqlite3 as _sq
    from datetime import datetime as _dt
    db = ROOT / "memory" / "dafeiyu" / "l1.db"
    if not db.exists():
        return
    try:
        con = _sq.connect(db)
        cand = con.execute("SELECT id,created FROM l1 WHERE state='candidate'").fetchall()
        act = con.execute("SELECT COUNT(*) FROM l1 WHERE state IN('active','protected')").fetchone()[0]
        con.close()
    except Exception:
        return
    if not cand:
        info.append(f"L1：活跃 {act}/10｜候选 0 条")
        return
    oldest = 0
    for _, c in cand:
        try:
            oldest = max(oldest, (_dt.now() - _dt.strptime((c or "")[:16], "%Y-%m-%d %H:%M")).days)
        except Exception:
            pass
    info.append(f"L1：活跃 {act}/10｜候选 {len(cand)} 条（最老 {oldest} 天）")
    if oldest >= 40:
        warn.append(f"L1：有候选放了 {oldest} 天没认领（跑 l1.py expire --apply 归档）")


def check_todo():
    """待办推送分级：不可逆/花钱/改内核/第三方/签发 → P0；其余攒着；没标注算 P0。"""
    items = _todo_items()
    if not items:
        return
    p0, p1 = [], []
    for item, mark in items:
        parts = [x.strip() for x in mark.split("·")] if mark else []
        owner = parts[0] if len(parts) > 0 else ""
        if not mark or owner not in ("她", "你") or len(parts) < 3:
            p0.append((item, "没标注（fail-closed）"))
            continue
        if any(k in mark for k in MARKS) or any(k in item for k in MARKS):
            p0.append((item, "含不可逆/花钱/内核/对外"))
        else:
            p1.append(item)

    for item, why in p0:
        warn.append(f"待办 P0：{item[:40]}（{why}）")

    if p1:
        import json as _j
        from datetime import datetime as _d
        try:
            TODO_LOG.parent.mkdir(parents=True, exist_ok=True)
            with TODO_LOG.open("a", encoding="utf-8") as f:
                for item in p1:
                    f.write(_j.dumps({"ts": _d.now().strftime("%Y-%m-%dT%H:%M"),
                                      "item": item, "rule": "可逆/免费/她的"},
                                     ensure_ascii=False) + "\n")
        except Exception:
            pass
        info.append(f"待办：P0 {len(p0)} 条｜被筛 {len(p1)} 条（可查 入口/_筛.jsonl）")
    elif p0:
        info.append(f"待办：P0 {len(p0)} 条")


def check_grant():
    """写权：有没有有效签发；有写操作却没签发 → 报错（N5 级）。"""
    import json as _json
    from datetime import datetime as _dt
    gf = ROOT / "入口" / "_签发.json"
    log = ROOT / "记忆" / "L3" / "_签发.jsonl"
    active = []
    if gf.exists():
        try:
            for g in _json.loads(gf.read_text(encoding="utf-8")).get("grants", []):
                if g.get("revoked"):
                    continue
                if _dt.strptime(g["expires"], "%Y-%m-%d %H:%M") > _dt.now():
                    active.append(g)
        except Exception:
            warn.append("签发文件读不出（_签发.json 可能坏了）")
    if active:
        g = active[-1]
        info.append(f"写权：有效签发 {g['id']}（{g['mode']}，到 {g['expires']}，{'、'.join(g['tools'])}）")
    else:
        info.append("写权：当前没有有效签发（只读；要写请 grant.py sign）")

    # 24h 内有没有「没有签发却尝试写」的记录
    if log.exists():
        denied = 0
        cutoff = _dt.now().timestamp() - 86400
        for line in log.read_text(encoding="utf-8").splitlines()[-200:]:
            if '"denied"' not in line:
                continue
            try:
                rec = _json.loads(line)
                ts = _dt.strptime(rec["ts"], "%Y-%m-%d %H:%M").timestamp()
                if ts >= cutoff:
                    denied += 1
            except Exception:
                continue
        if denied:
            warn.append(f"写权：24 小时内有 {denied} 次无签发的写尝试（已留痕）")


def check_workbench():
    """任务看板：条数/候选 id/汉字。不阻止开场。"""
    rc, out, _ = sh([str(ROOT / "脚本" / "workbench.py"), "prune"])
    line = [l for l in out.splitlines() if l.startswith("已达：")]
    cands = [l for l in out.splitlines() if l.startswith("[")]
    msg = line[-1] if line else ""
    if cands:
        # 候选要带 id —— 不然她只知道"有几条"，不知道退哪条
        import re as _re
        ids = []
        for l in cands:
            m = _re.search(r"\]\s*(D-\d+)", l)
            if m:
                ids.append(m.group(1))
        id_txt = "、".join(ids) if ids else f"{len(cands)} 条"
        warn.append(f"任务看板：候选 {id_txt}｜{msg}")
    elif msg:
        info.append(f"任务看板：{msg}")


def heartbeat(mode):
    """开场写一行：这台机器上"上一次聊天"就是上一行。

    三条防污染规则（2026-10-28）：
      · 30 分钟内重复跑（体检/自检/手工点）不再写第二行；
      · `preflight --no-heartbeat` 时不写；
      · 文件超过 500 行，只留最近 200 行。
    """
    import json as _json
    from datetime import datetime as _dt
    if "--no-heartbeat" in sys.argv:
        return
    SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if SESSION_LOG.exists():
        lines = [l for l in SESSION_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    if lines:
        try:
            last = _json.loads(lines[-1]).get("ts", "")
            if last:
                gap = (_dt.now() - _dt.strptime(last, "%Y-%m-%dT%H:%M")).total_seconds()
                if 0 <= gap < 1800:
                    return
        except Exception:
            pass
    rec = {"ts": _dt.now().strftime("%Y-%m-%dT%H:%M"), "mode": mode}
    lines.append(_json.dumps(rec, ensure_ascii=False))
    if len(lines) > 500:
        lines = lines[-200:]
    SESSION_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_index():
    if not FTS.exists():
        warn.append("L3 检索索引还没建（python 脚本/l3_index.py build）")
        return
    age_h = (datetime.now().timestamp() - FTS.stat().st_mtime) / 3600
    if age_h > 24:
        warn.append(f"L3 检索索引 {age_h:.0f} 小时没更新（收尾会自动更新）")
    rc, out, _ = sh([str(ROOT / "脚本" / "l3.py"), "check"])
    if rc != 0:
        for line in out.splitlines():
            if line.strip().startswith("-"):
                warn.append("L3 校验：" + line.strip())
    else:
        info.append("L3：索引与文件对得上")


def check_budget():
    rc, out, _ = sh([str(ROOT / "脚本" / "budget.py")])
    last = out.strip().splitlines()[-1] if out.strip() else ""
    if rc != 0:
        warn.append("预算：" + last)


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="开场自检")
    ap.add_argument("--card", default="")
    ap.add_argument("--mode", default="日常", choices=["日常", "工作", "完整"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-heartbeat", action="store_true",
                    help="不写会话心跳（体检/自检/脚本里调用时用，避免污染「上一次聊天」）")
    a = ap.parse_args()

    card = a.card
    if not card:
        try:
            reg = json.loads((DATA / "cards" / "_registry.json").read_text(encoding="utf-8-sig"))
            card = reg.get("default_card", "dafeiyu")
        except Exception:
            card = "dafeiyu"

    check_load(card, a.mode)
    check_l0()
    check_memory()
    check_close()
    check_open_items()
    check_pending_all()
    check_l1()
    check_todo()
    check_grant()
    check_workbench()
    check_trip_memory()
    check_pack_memory(a.mode)
    check_pending_close()
    check_index()
    check_budget()

    heartbeat(a.mode)

    if a.json:
        print(json.dumps({"ok": not err, "info": info, "warn": warn, "err": err},
                         ensure_ascii=False, indent=2))
    else:
        print(f"=== 开场自检（{card} / {a.mode}）===")
        for x in info:
            print(f"  [OK] {x}")
        for x in warn:
            print(f"  ! {x}")
        for x in err:
            print(f"  [X] {x}")
        print()
        if err:
            print("结论：有硬错误，先修再开始。")
        elif warn:
            print("结论：可以开始，但有上面几处要注意。")
        else:
            print("结论：一切正常。")

    sys.exit(2 if err else (1 if warn else 0))


if __name__ == "__main__":
    main()
