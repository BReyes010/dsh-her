#!/usr/bin/env python3
"""policy：审核等级（strict / simple）的唯一入口。

用法：
  python 脚本/policy.py                 # 看当前等级
  python 脚本/policy.py set simple --why "自己用，嫌烦"
  python 脚本/policy.py set strict --why "要发版了"
  python 脚本/policy.py log             # 最近的切换记录
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ["HER_HOME"]).resolve() if os.environ.get("HER_HOME") else ROOT
CFG = DATA / "规格" / "审核等级.json"
LOG = DATA / "记忆" / "L3" / "_审核等级.jsonl"
DEFAULT = "strict"

# 简易模式下"免核实"的类目白名单；不在表里的一律仍要签。
# 判据是**类目字段**，不是内容语义（数字/他人的话只是提示，不自动升级为拦截）。
AUTO_APPROVE = [
    "工作方式", "成长记录", "偏好", "反思", "总结",
    "他人原话", "数字", "数据",
]
# 永远要签的类目（即使 simple）
ALWAYS_SIGN = ["身份与来历"]


def auto_approve_category(category):
    """这条类目在 simple 下能不能直接转正。"""
    if not is_simple():
        return False, "严格模式：一律要签"
    c = (category or "").strip()
    if c in ALWAYS_SIGN:
        return False, f"「{c}」始终要签"
    if c in AUTO_APPROVE:
        return True, f"简易模式：类目「{c}」自动转正"
    return False, f"「{c or '未填类目'}」不在自动转正白名单"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def level():
    try:
        return json.loads(CFG.read_text(encoding="utf-8")).get("level", DEFAULT)
    except Exception:
        return DEFAULT


def is_simple():
    return level() == "simple"


def _log(rec):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), **rec}, ensure_ascii=False) + "\n")


def set_level(lv, why, by):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    old = level()
    CFG.write_text(json.dumps({"level": lv, "updated": now(), "by": by,
                               "why": why}, ensure_ascii=False, indent=2), encoding="utf-8")
    _log({"event": "set", "from": old, "to": lv, "why": why, "by": by})
    return old


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="审核等级")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("set")
    p.add_argument("level", choices=["strict", "simple"])
    p.add_argument("--why", default="")
    p.add_argument("--by", default="maker")
    sub.add_parser("log")
    a = ap.parse_args()
    if a.cmd == "set":
        old = set_level(a.level, a.why, a.by)
        print(f"[OK] 审核等级 {old} → {a.level}（已留痕）")
        if a.level == "simple":
            print("     简易：写记忆/交接免签；**改 L0 与任务卡恢复仍要人工**")
        else:
            print("     严格：所有写操作都要签发")
        return
    if a.cmd == "log":
        if LOG.exists():
            for line in LOG.read_text(encoding="utf-8").splitlines()[-10:]:
                print(line)
        else:
            print("（没有记录）")
        return
    lv = level()
    print(f"当前审核等级：{lv}" + ("（写记忆/交接免签）" if lv == "simple" else "（全部要签发）"))
    if lv == "simple":
        print("  自动转正类目：" + "、".join(AUTO_APPROVE))
        print("  仍要签字类目：" + "、".join(ALWAYS_SIGN) + "（以及不在白名单里的）")
    for x in ("L0 人格卡改动", "任务卡恢复"):
        print(f"  {x}：始终要人工")


if __name__ == "__main__":
    main()
