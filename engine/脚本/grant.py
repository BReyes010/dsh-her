#!/usr/bin/env python3
"""grant —— 会话级写权签发（judge = 哪一次签发，不是哪台机器）。

用法：
  python 脚本/grant.py sign --tools write:handoff,write:memory --hours 4 --reason "今晚收尾"
  python 脚本/grant.py sign --tools write:state --by-request         # A 模式：逐次点（1 小时、1 次）
  python 脚本/grant.py list
  python 脚本/grant.py revoke --id g-xxxx

规则（见 规格/写权-契约.md）：
  · 每个开场窗口可签 1 次；单次 ≤4 小时；24 小时累计 ≤8 小时
  · 超限不禁止写，只禁止 B 模式（签不出 4 小时的，退到 A 模式短签）
  · 工具集固定：write:memory / write:handoff / write:state（state 只走 A）
  · 签发本身记账（系统侧写 _签发.jsonl，不依赖 token）
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ["HER_HOME"]).resolve() if _os.environ.get("HER_HOME") else ROOT
ENTRY = DATA / "入口"
GRANTS = ENTRY / "_签发.json"
LOG = DATA / "记忆" / "L3" / "_签发.jsonl"

ALLOWED = {"write:memory", "write:handoff", "write:state"}
B_MAX_HOURS = 4
DAY_CAP_HOURS = 8
A_HOURS = 1
A_PER_DAY = 3          # 每天最多签 3 次 A（次数而非小时）
KEEP_DAYS = 3          # 过期/已用票在文件里再留几天，之后自动清（审计日志不动）


def now():
    return datetime.now()


def prune(d):
    """自动清理：过期超过 KEEP_DAYS 的、已用超过 KEEP_DAYS 的（审计日志不动）。"""
    from datetime import timedelta as _td
    keep, dropped = [], 0
    for x in d.get("grants", []):
        ref = x.get("consumed_at") or x.get("expires") or ""
        try:
            t = datetime.strptime(ref[:16], "%Y-%m-%d %H:%M")
        except Exception:
            keep.append(x)
            continue
        if now() - t > _td(days=KEEP_DAYS):
            dropped += 1
            continue
        keep.append(x)
    d["grants"] = keep
    return dropped


def load():
    if not GRANTS.exists():
        return {"grants": []}
    try:
        return json.loads(GRANTS.read_text(encoding="utf-8"))
    except Exception:
        return {"grants": []}


def save(d):
    ENTRY.mkdir(parents=True, exist_ok=True)
    GRANTS.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def log(rec):
    """系统侧记账：不依赖 token，写不了就报错（调用方据此拒写）。"""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def used_hours(d, ref=None):
    """最近 24 小时内 **B 模式**签出去的窗口小时数。

    A 模式是"单次票"，按**次数**算（每天 ≤ A_PER_DAY），不占小时额度——
    两套口径分开，别混在一个数里（否则一张 1 小时的 A 票会吃掉 B 的额度）。
    """
    ref = ref or now()
    total = 0.0
    for g in d["grants"]:
        if g.get("mode") == "A":
            continue
        try:
            st = datetime.strptime(g["start"], "%Y-%m-%d %H:%M")
        except Exception:
            continue
        if (ref - st).total_seconds() <= 86400:
            total += float(g.get("hours", 0))
    return total


def used_a_today(d):
    today = now().strftime("%Y-%m-%d")
    return sum(1 for x in d.get("grants", [])
               if x.get("mode") == "A" and str(x.get("start", ""))[:10] == today)


BUNDLES = {
    # 收尾是一个动作，不是三次写：一张票给足件数
    "收尾": {"tools": ["write:handoff", "write:memory"], "uses": 4},
}


def cmd_sign(a):
    if getattr(a, "purpose", "") == "收尾":
        b = BUNDLES["收尾"]
        a.tools = ",".join(b["tools"])
    tools = [t.strip() for t in a.tools.split(",") if t.strip()]
    bad = [t for t in tools if t not in ALLOWED]
    if bad:
        print(f"[拒] 未知工具 {bad}；只允许 {sorted(ALLOWED)}")
        return 2
    if not tools:
        print("[拒] 工具集不能为空")
        return 2

    d = load()
    dropped = prune(d)
    if dropped:
        save(d)
        print(f"[清理] 自动清掉 {dropped} 张过期/已用票（审计日志保留）")
    used = used_hours(d)

    mode_a = "write:state" in tools or a.by_request

    # A 模式：每天最多 3 次（次数上限，不是小时上限）
    if mode_a:
        used_a = used_a_today(d)
        if used_a >= A_PER_DAY:
            print(f"[拒] 今天 A 模式已签 {used_a} 次（上限 {A_PER_DAY}）；"
                  "等 24h 窗口滑出，或让 maker 用 B 模式", file=sys.stderr)
            return 3
    hours = A_HOURS if mode_a else min(a.hours, B_MAX_HOURS)

    # B 模式受 24h 累计上限约束；超限退到 A（不禁止写）
    if not mode_a and used + hours > DAY_CAP_HOURS:
        hours = A_HOURS
        mode_a = True
        print(f"[提示] 24h 累计已 {used:.1f}h，B 模式签不出 → 退到 A 模式（{A_HOURS}h，逐次点）")

    start = now()
    rec = {
        "id": f"g-{start.strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}",
        "start": start.strftime("%Y-%m-%d %H:%M"),
        "expires": (start + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M"),
        "hours": hours,
        "mode": "A" if mode_a else "B",
        "tools": tools,
        "by": a.by,
        "reason": a.reason or "",
        "session": a.session or "auto",
        "uses": (BUNDLES["收尾"]["uses"] if getattr(a, "purpose", "") == "收尾"
                 else (1 if mode_a else 0)),   # N = 可用 N 次；0 = 不限
        "purpose": getattr(a, "purpose", ""),
        "consumed": False,
    }
    d["grants"].append(rec)
    save(d)
    log({"ts": start.strftime("%Y-%m-%d %H:%M"), "event": "sign", **rec})
    print(f"[已签发] {rec['id']} {rec['mode']} 模式 {hours}h 到 {rec['expires']}")
    print(f"         工具：{'、'.join(tools)}｜理由：{rec['reason'] or '（无）'}")
    return 0


def cmd_list(a):
    d = load()
    dropped = prune(d)
    if dropped:
        save(d)
        print(f"[清理] 自动清掉 {dropped} 张过期/已用票（审计日志保留）")
    act = [g for g in d["grants"]
           if datetime.strptime(g["expires"], "%Y-%m-%d %H:%M") > now()]
    print(f"共 {len(d['grants'])} 条｜有效 {len(act)} 条｜"
          f"B 已用 {used_hours(d):.1f}h / {DAY_CAP_HOURS}h｜今日 A {used_a_today(d)} / {A_PER_DAY} 次")
    for g in act:
        print(f"  {g['id']}  {g['mode']}  {g['start']} → {g['expires']}  {','.join(g['tools'])}  {g['reason']}")
    return 0


def cmd_revoke(a):
    d = load()
    hit = [g for g in d["grants"] if g["id"] == a.id]
    if not hit:
        print(f"[拒] 没有 {a.id}")
        return 2
    hit[0]["revoked"] = True
    save(d)
    log({"ts": now().strftime("%Y-%m-%d %H:%M"), "event": "revoke", "id": a.id, "by": a.by})
    print(f"[已撤销] {a.id}")
    return 0


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="会话级写权签发")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sign")
    p.add_argument("--tools", default="", help="逗号分隔：write:memory,write:handoff,write:state；用 --purpose 收尾 时可省")
    p.add_argument("--hours", type=float, default=B_MAX_HOURS)
    p.add_argument("--by", default="maker")
    p.add_argument("--reason", default="")
    p.add_argument("--purpose", default="", choices=["", "收尾"],
                   help="收尾＝一张票含 handoff 1 次 + 触动 3 次（合计 4 次写）")
    p.add_argument("--session", default="")
    p.add_argument("--by-request", action="store_true", help="A 模式：1 小时、单次")
    sub.add_parser("list")
    p = sub.add_parser("revoke")
    p.add_argument("--id", required=True)
    p.add_argument("--by", default="maker")

    a = ap.parse_args()
    return {"sign": cmd_sign, "list": cmd_list, "revoke": cmd_revoke}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
