#!/usr/bin/env python3
"""L1 自我认知层（SQLite 真源）。

按 `人格/L1-规范.md` 实现：候选区 → 签字 → 活跃区（≤10 行）→ PROTECTED → 毕业/回收。
- 真源：`人格/l1.db`（表 l1 / l1_history / l1_changelog）
- 注入：只输出 rule 一行，两个投影都注入（D13 定案）
- 证据：refs 指向 `记忆/L3/index.csv` + `#NNN-事件.json`，解析不到即无效
- 权限：agent 只能 propose；sign/reclaim/graduate 是 maker 的动作

用法：
  python l1.py init
  python l1.py propose --rule "讲错话先给结论，再补细节" --refs "#002,#007" \
        --why "两次都是先道歉再解释被指出绕" --impact "改下一期开场三句" \
        --reclaim "连续 3 期开场首句即结论 → 可回收"
  python l1.py sign --id L1-03 --ep 008
  python l1.py list
  python l1.py inject
  python l1.py review --ep 020
  python l1.py reclaim --id L1-03 --reason "连续 3 期已达标"
  python l1.py graduate --id L1-03 --to l0 --reason "季度毕业，进步类"
  python l1.py changelog

退出码：0 正常；2 参数/数据问题；3 违规（证据不足、满队、状态不允许）。
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from paths import rel

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
from paths import paths as _paths
_P = _paths()
DB = _P["l1"]
L3 = _P["l3"]
INDEX = L3 / "index.csv"
MAX_ACTIVE = 10
PROTECT_AGE = 8
GRADUATE_AGE = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS l1(
  id TEXT PRIMARY KEY, rule TEXT NOT NULL, refs TEXT NOT NULL, why TEXT NOT NULL,
  impact TEXT NOT NULL, reclaim TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'candidate',
  age_from TEXT, activated TEXT, protected_at TEXT, review_due TEXT,
  source TEXT DEFAULT 'reflection', provisional INTEGER DEFAULT 0,
  created TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS l1_history(
  id TEXT, rule TEXT, refs TEXT, why TEXT, impact TEXT, reclaim TEXT,
  state TEXT, reason TEXT, retired_at TEXT, ep TEXT
);
CREATE TABLE IF NOT EXISTS l1_changelog(
  ts TEXT, action TEXT, id TEXT, detail TEXT
);
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def connect(create=False):
    if not DB.exists() and not create:
        sys.exit("l1.db 不存在，先跑 init")
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def log(con, action, lid, detail=""):
    con.execute("INSERT INTO l1_changelog(ts,action,id,detail) VALUES(?,?,?,?)",
                (now(), action, lid, detail))


def parse_ep(tag):
    """'#002' -> ('2026','002')；'2027#012' -> ('2027','012')"""
    tag = tag.strip().lstrip("#")
    if "#" in tag:
        year, ep = tag.split("#")
    else:
        year, ep = "2026", tag
    return year, ep.zfill(3)


HANDOFF = L3 / "handoff"
NOTES = ROOT / "记忆" / "L3" / "触动.md"


def check_refs(con, refs):
    """证据校验：期/事件（L3 事件 JSON）、会话（handoff 文件）、笔记（触动条目）。

    2026-10-29 按{角色名}意见扩：成长不只来自工作，"日常聊天/看视频"也能当证据。
    规则：≥2 条，且**来源不能全相同**（三条都是同一个会话 = 一次事件，不算双证据）。
    """
    tags = [t for t in re.split(r"[,，\s]+", refs) if t]
    if len(tags) < 2:
        return False, f"证据不足：只有 {len(tags)} 条，需要 ≥2 条", tags

    ok_tags, missing = [], []
    for t in tags:
        if t.startswith("会话#"):
            stem = t.split("#", 1)[1]
            hit = list(HANDOFF.glob(f"*{stem}*.md")) if HANDOFF.exists() else []
            if not hit:
                missing.append(f"{t}（handoff 里没这个会话）")
            else:
                ok_tags.append(("会话", stem))
        elif t.startswith("笔记#"):
            key = t.split("#", 1)[1]
            if not NOTES.exists() or key not in NOTES.read_text(encoding="utf-8"):
                missing.append(f"{t}（触动.md 里找不到「{key}」）")
            else:
                ok_tags.append(("笔记", key))
        else:
            year, ep = parse_ep(t)
            f = L3 / year / f"#{ep}-事件.json"
            if not f.exists():
                missing.append(f"{t}（缺 {rel(f)}）")
                continue
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                missing.append(f"{t}（JSON 解析失败：{e}）")
                continue
            ok_tags.append(("期", ep))

    if missing:
        return False, "证据无效：" + "；".join(missing), tags
    kinds = {k for k, _ in ok_tags}
    uniq = {(k, v) for k, v in ok_tags}
    if len(uniq) < 2:
        return False, "证据重复：两条其实是同一处，不算双证据", tags
    if len(uniq) == 2 and len(kinds) == 1 and all(k == "期" for k, _ in uniq):
        return False, "期证据要求来自不同期（现行规则）", tags
    return True, f"ok（{len(uniq)} 条：{'、'.join(sorted(kinds))}）", tags


def cmd_init(_):
    con = connect(create=True)
    con.commit()
    print(f"已建 {DB}")
    con.close()


def cmd_propose(a):
    con = connect()
    cols = {r[1] for r in con.execute("PRAGMA table_info(l1)")}
    for c, ddl in (("occasion", "TEXT DEFAULT '工作'"), ("impact_kind", "TEXT DEFAULT '做法'"),
                   ("honest", "INTEGER DEFAULT 0"), ("trigger_at", "TEXT DEFAULT ''"),
                   ("observable", "TEXT DEFAULT ''"), ("wrong_if_missing", "TEXT DEFAULT ''"),
                   ("merged_into", "TEXT DEFAULT ''")):
        if c not in cols:
            con.execute(f"ALTER TABLE l1 ADD COLUMN {c} {ddl}")
    con.commit()
    if not a.rule.strip():
        sys.exit(2)
    dup = con.execute("SELECT id FROM l1 WHERE rule=?", (a.rule,)).fetchone()
    if dup:
        print(f"重复：已有 {dup['id']} 用同一句", file=sys.stderr)
        sys.exit(3)
    # ① honest 限配额：最近 10 条候选里最多 1 条 honest（否则出口会吃掉门槛）
    recent10 = con.execute(
        "SELECT honest FROM l1 WHERE state='candidate' ORDER BY created DESC, id DESC LIMIT 10").fetchall()
    if a.honest and sum(1 for r in recent10 if r["honest"]) >= 1:
        print("honest 出口已用过一次（最近 10 条候选里已有 1 条），"
              "先等 maker 过目那条，再走这个出口", file=sys.stderr)
        sys.exit(3)

    # ② 配额按「证据类型」算，不按人填的域（防把聊天拆成日常/社交/闲聊绕过）
    def src_kind(refs_text):
        kinds = set()
        for t in re.split(r"[,，\s]+", refs_text or ""):
            if not t:
                continue
            if t.startswith("会话#"):
                kinds.add("会话")
            elif t.startswith("笔记#"):
                kinds.add("笔记")
            else:
                kinds.add("期")
        return "混合" if len(kinds) > 1 else (kinds.pop() if kinds else "未知")

    now_kind = src_kind(a.refs)
    recent3 = con.execute("SELECT refs FROM l1 ORDER BY created DESC, id DESC LIMIT 3").fetchall()
    if len(recent3) == 3 and all(src_kind(r["refs"]) == now_kind for r in recent3):
        print(f"配额：最近 3 条都以「{now_kind}」类证据为主，换一类来源再来"
              "（要防的正是全是聊天）", file=sys.stderr)
        sys.exit(3)

    # ③ observable 必须是「已经发生过的观察」，不是将来的承诺
    if not a.honest and a.observable.strip():
        past = ("已", "上次", "那天", "这周", "刚才", "了", "过")
        future = ("会", "将来", "以后", "下次", "将要")
        has_past = any(w in a.observable for w in past)
        has_future = any(w in a.observable for w in future)
        if has_future and not has_past:
            print("observable 要写「已经发生过的观察」，不是「将来别人会看到」。"
                  "例：『上周被指出绕的时候，我停了半分钟才接话』", file=sys.stderr)
            sys.exit(3)

    # 收紧准入（maker 2026-10-29）：触发条件 / 可观察差异 / 不遵守会出什么错
    if not a.honest:
        if not a.impact.strip():
            print("非 honest 时必须写 impact（改变的具体做法/反应）", file=sys.stderr)
            sys.exit(3)
        miss = [n for n, v in (("trigger 触发条件", a.trigger),
                               ("observable 可观察差异", a.observable),
                               ("wrong_if_missing 不遵守会出什么错", a.wrong)) if not str(v).strip()]
        if miss:
            print("准入不通过，缺：" + "；".join(miss)
                  + "。指不出就加 --honest（诚实出口不算丢人，编才丢人）", file=sys.stderr)
            sys.exit(3)

    # 反应型 / 诚实出口：都不算不合格，但必须写明
    if a.honest and not a.impact.strip():
        pass
    elif a.impact_kind == "反应" and not a.impact.strip():
        print("反应型也要写清：遇到这种场合会停在哪一步", file=sys.stderr)
        sys.exit(3)

    ok, msg, tags = check_refs(con, a.refs)
    if not ok and not a.skip_ref_check:
        print(msg, file=sys.stderr)
        sys.exit(3)
    if ok is False and a.skip_ref_check:
        msg += "（未校验，测试用）"
    active = con.execute("SELECT COUNT(*) c FROM l1 WHERE state IN('active','protected')").fetchone()["c"]
    if active >= MAX_ACTIVE and not a.force:
        print(f"BLOCKED_CAPACITY：活跃 {active}/{MAX_ACTIVE} 行，先毕业或回收一条", file=sys.stderr)
        sys.exit(3)
    nxt = con.execute("SELECT COUNT(*) c FROM l1").fetchone()["c"] + 1
    lid = f"L1-{nxt:02d}"
    while con.execute("SELECT 1 FROM l1 WHERE id=?", (lid,)).fetchone():
        nxt += 1
        lid = f"L1-{nxt:02d}"
    con.execute(
        "INSERT INTO l1(id,rule,refs,why,impact,reclaim,state,source,provisional,created,updated,"
        " occasion,impact_kind,honest,trigger_at,observable,wrong_if_missing)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (lid, a.rule.strip(), a.refs, a.why,
         a.impact or ("指不出做法，先放着" if a.honest else ""),
         a.reclaim, "candidate",
         a.source, 1 if a.provisional else 0, now(), now(),
         a.occasion, a.impact_kind, 1 if a.honest else 0,
         a.trigger, a.observable, a.wrong))
    log(con, "propose", lid, msg)
    con.commit()
    print(f"候选已入区：{lid}  （state=candidate，不占 10 行、不注入）")
    print(f"证据校验：{msg}")
    con.close()


def cmd_merge(a):
    """把 --drop 合并进 --keep：refs 取并集，drop 标 merged（不删，留历史）。"""
    con = connect()
    keep = con.execute("SELECT * FROM l1 WHERE id=?", (a.keep,)).fetchone()
    drop = con.execute("SELECT * FROM l1 WHERE id=?", (a.drop,)).fetchone()
    if not keep or not drop:
        print("keep/drop 里有一个不存在", file=sys.stderr)
        sys.exit(2)
    if a.keep == a.drop:
        sys.exit(2)
    if not a.why.strip():
        print("合并必须写理由：为什么这两条是同一条（不写理由的合并＝掩盖分歧）", file=sys.stderr)
        sys.exit(3)
    refs = []
    for r in (keep["refs"], drop["refs"]):
        for t in re.split(r"[,，\s]+", r or ""):
            if t and t not in refs:
                refs.append(t)
    con.execute("UPDATE l1 SET refs=?, updated=? WHERE id=?", (", ".join(refs), now(), a.keep))
    con.execute("UPDATE l1 SET state='merged', merged_into=?, updated=? WHERE id=?",
                (a.keep, now(), a.drop))
    con.execute(
        "INSERT INTO l1_history(id,rule,refs,why,impact,reclaim,state,reason,retired_at,ep)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (drop["id"], drop["rule"], drop["refs"], drop["why"], drop["impact"], drop["reclaim"],
         "merged", f"并入 {a.keep}：{a.why}", now(), ""))
    log(con, "merge", a.drop, f"→ {a.keep}：{a.why}")
    con.commit()
    print(f"{a.drop} 已并入 {a.keep}（refs 合并为 {len(refs)} 条）；历史留痕")
    con.close()


def cmd_expire(a):
    """候选放太久没人认领 → 归档（不删，可查可复活）。默认 40 天。"""
    from datetime import datetime as _dt, timedelta as _td
    con = connect()
    rows = con.execute("SELECT * FROM l1 WHERE state='candidate'").fetchall()
    horizon = _dt.now() - _td(days=a.days)
    old = []
    for r in rows:
        try:
            c = _dt.strptime((r["created"] or "")[:16], "%Y-%m-%d %H:%M")
        except Exception:
            continue
        if c < horizon:
            old.append(r)
    if not old:
        print(f"候选 {len(rows)} 条，没有超过 {a.days} 天的")
        con.close()
        return
    print(f"超过 {a.days} 天的候选：{len(old)} 条 → {[r['id'] for r in old]}")
    if not a.apply:
        print("只报不改。要归档加 --apply（进历史，可查）")
        con.close()
        return
    for r in old:
        con.execute("UPDATE l1 SET state='expired', updated=? WHERE id=?", (now(), r["id"]))
        con.execute(
            "INSERT INTO l1_history(id,rule,refs,why,impact,reclaim,state,reason,retired_at,ep)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["rule"], r["refs"], r["why"], r["impact"], r["reclaim"],
             "expired", f"候选超过 {a.days} 天没人认领", now(), ""))
        log(con, "expire", r["id"], f"{a.days} 天未认领")
    con.commit()
    print(f"已归档 {len(old)} 条（state=expired，历史可查）")
    con.close()


def cmd_sign(a):
    con = connect()
    r = con.execute("SELECT * FROM l1 WHERE id=?", (a.id,)).fetchone()
    if not r:
        sys.exit(2)
    if r["state"] != "candidate":
        print(f"{a.id} 当前 state={r['state']}，只能签 candidate", file=sys.stderr)
        sys.exit(3)
    active = con.execute("SELECT COUNT(*) c FROM l1 WHERE state IN('active','protected')").fetchone()["c"]
    if active >= MAX_ACTIVE:
        print(f"BLOCKED_CAPACITY：活跃 {active}/{MAX_ACTIVE}", file=sys.stderr)
        sys.exit(3)
    review = str(int(a.ep) + GRADUATE_AGE).zfill(3)
    con.execute("UPDATE l1 SET state='active', age_from=?, activated=?, review_due=?, updated=? WHERE id=?",
                (a.ep, a.ep, review, now(), a.id))
    log(con, "sign", a.id, f"生效于 #{a.ep}，review_due #{review}")
    con.commit()
    print(f"{a.id} 已生效（active），期龄从 #{a.ep} 起算，核查点 #{review}")
    con.close()


def cmd_list(_):
    con = connect()
    rows = con.execute("SELECT * FROM l1 ORDER BY id").fetchall()
    if not rows:
        print("（空）——一条都还没有")
        return
    print(f"{'ID':<7}{'状态':<11}{'起算':<6}{'核查':<6}规则")
    print("-" * 90)
    for r in rows:
        print(f"{r['id']:<7}{r['state']:<11}{(r['age_from'] or '—'):<6}{(r['review_due'] or '—'):<6}{r['rule']}")
    active = sum(1 for r in rows if r["state"] in ("active", "protected"))
    print(f"\n活跃 {active}/{MAX_ACTIVE}　候选 {sum(1 for r in rows if r['state']=='candidate')}")
    con.close()


def cmd_inject(_):
    """注入：rule + 一句触发条件（2026-10-29 加 trigger，让原则可执行而不是口号）。"""
    con = connect()
    cols = {r[1] for r in con.execute("PRAGMA table_info(l1)")}
    sel = "id,rule,trigger_at" if "trigger_at" in cols else "id,rule,''"
    rows = con.execute(
        f"SELECT {sel} FROM l1 WHERE state IN('active','protected') ORDER BY id").fetchall()
    if not rows:
        print("", end="")
        return
    print("## 自我认知（她学会的事）")
    for r in rows:
        trig = (r["trigger_at"] or "").strip() if "trigger_at" in cols else ""
        if trig:
            print(f"- {r['rule']}（{trig}）")
        else:
            print(f"- {r['rule']}")
    con.close()


def cmd_review(a):
    con = connect()
    ep = a.ep
    rows = con.execute("SELECT * FROM l1 WHERE state IN('active','protected') ORDER BY id").fetchall()
    flags = []
    for r in rows:
        age = int(ep) - int(r["age_from"])
        if r["state"] == "active" and age >= PROTECT_AGE and not r["protected_at"]:
            con.execute("UPDATE l1 SET state='protected', protected_at=?, updated=? WHERE id=?",
                        (ep, now(), r["id"]))
            log(con, "protect", r["id"], f"期龄 {age} ≥ {PROTECT_AGE}")
            flags.append(f"{r['id']} → PROTECTED（期龄 {age}）")
        if r["review_due"] and int(ep) >= int(r["review_due"]):
            flags.append(f"{r['id']} 到核查点 #{r['review_due']}：检查 reclaim「{r['reclaim']}」")
        if age >= GRADUATE_AGE and r["state"] == "protected":
            flags.append(f"{r['id']} 满足毕业期龄（{age}≥{GRADUATE_AGE}）→ 季度反思时交你裁决")
    con.commit()
    if not flags:
        print(f"第 {ep} 期：无需动作（活跃 {len(rows)} 条）")
    else:
        print(f"第 {ep} 期：")
        for f in flags:
            print(f"- {f}")
    con.close()


def _retire(con, lid, state, reason, ep):
    r = con.execute("SELECT * FROM l1 WHERE id=?", (lid,)).fetchone()
    if not r:
        sys.exit(2)
    con.execute("INSERT INTO l1_history(id,rule,refs,why,impact,reclaim,state,reason,retired_at,ep)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (r["id"], r["rule"], r["refs"], r["why"], r["impact"], r["reclaim"],
                 state, reason, now(), ep))
    con.execute("DELETE FROM l1 WHERE id=?", (lid,))
    log(con, state, lid, reason)
    con.commit()
    print(f"{lid} → {state}，已搬进 l1_history（号不复用，D16）")


def cmd_reclaim(a):
    con = connect()
    _retire(con, a.id, "retired", a.reason, a.ep)
    con.close()


def cmd_graduate(a):
    con = connect()
    _retire(con, a.id, "graduated:" + a.to, a.reason, a.ep)
    con.close()


def cmd_changelog(_):
    con = connect()
    for r in con.execute("SELECT * FROM l1_changelog ORDER BY rowid DESC LIMIT 30"):
        print(f"{r['ts']}  {r['action']:<9} {r['id'] or '—':<7} {r['detail'] or ''}")
    con.close()


def main():
    ap = argparse.ArgumentParser(description="L1 自我认知层")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)

    p = sub.add_parser("propose")
    p.add_argument("--rule", required=True)
    p.add_argument("--refs", required=True)
    p.add_argument("--why", required=True)
    p.add_argument("--impact", default="", help="honest 时可空")
    p.add_argument("--reclaim", required=True)
    p.add_argument("--impact-kind", default="做法", choices=["做法", "反应"],
                   help="改变做法，还是改变反应（停/不说/不装懂）")
    p.add_argument("--occasion", default="工作",
                   choices=["工作", "日常", "娱乐", "学习", "关系"],
                   help="来源域（白名单，防越分越细）；配额不按它算，按证据类型算")
    p.add_argument("--trigger", default="", help="触发条件：什么信号出现时生效")
    p.add_argument("--observable", default="", help="可观察差异：别人能看出什么不同")
    p.add_argument("--wrong-if-missing", dest="wrong", default="",
                   help="不遵守会出什么具体错（写不出=不合格）")
    p.add_argument("--honest", action="store_true",
                   help="诚实出口：指不出做法，先放着（不算不合格）")
    p.add_argument("--source", default="reflection")
    p.add_argument("--provisional", action="store_true")
    p.add_argument("--skip-ref-check", action="store_true", help="仅测试用：跳过 L3 证据校验")
    p.add_argument("--force", action="store_true", help="满队也强插候选")
    p.set_defaults(fn=cmd_propose)

    p = sub.add_parser("sign"); p.add_argument("--id", required=True); p.add_argument("--ep", required=True); p.set_defaults(fn=cmd_sign)
    p = sub.add_parser("merge"); p.add_argument("--keep", required=True); p.add_argument("--drop", required=True); p.add_argument("--why", required=True); p.set_defaults(fn=cmd_merge)
    p = sub.add_parser("expire"); p.add_argument("--days", type=int, default=40); p.add_argument("--apply", action="store_true"); p.set_defaults(fn=cmd_expire)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("inject").set_defaults(fn=cmd_inject)
    p = sub.add_parser("review"); p.add_argument("--ep", required=True); p.set_defaults(fn=cmd_review)
    p = sub.add_parser("reclaim"); p.add_argument("--id", required=True); p.add_argument("--reason", required=True); p.add_argument("--ep", required=True); p.set_defaults(fn=cmd_reclaim)
    p = sub.add_parser("graduate"); p.add_argument("--id", required=True); p.add_argument("--to", choices=["l0", "sample"], required=True); p.add_argument("--reason", required=True); p.add_argument("--ep", required=True); p.set_defaults(fn=cmd_graduate)
    sub.add_parser("changelog").set_defaults(fn=cmd_changelog)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
