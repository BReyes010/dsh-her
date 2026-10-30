#!/usr/bin/env python3
"""预算检查器：量各块体积，对照 v0.6.0 的目标 / 提示 / 硬顶。

用法：
  python 脚本/budget.py            # 表格 + 结论
  python 脚本/budget.py --json     # 机器可读

退出码：0 全部在目标内；1 有块越过"提示线"；2 有块越"硬顶"。
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
HOST = DATA / "入口" / "her-host.py"
from paths import paths as _paths
_P = _paths()
CARD = Path(_P["scenes"]["日常"][0])
WORK = Path(_P["scenes"]["口播"][1])
TECH = Path(_P["scenes"]["口播"][2])
DESK = _P["desk"]

# v0.6.0 上限（token；汉字×1.5–2.0 估算）
CAPS = {
    "内核": {"target": 4000, "warn": 6000, "hard": 8000},
    "日常投影": {"target": 5600, "warn": 6500, "hard": 7600},
    "工作投影": {"target": 11000, "warn": 12500, "hard": 14000},
    "技术规范": {"target": None, "warn": None, "hard": None},   # 不计预算
    "移植包": {"target": 5000, "warn": 5500, "hard": 6000},
}


def han(text):
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def tokens(chars):
    return round(chars * 1.5), round(chars * 2.0)


def read(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def pack(scene):
    # 口径必须跟实际启动包一致：日常 now / 工作 brief / 完整 full
    desk = {"日常": "now", "工作": "brief", "完整": "full"}.get(scene, "full")
    p = subprocess.run([sys.executable, str(HOST), "--print", scene, "--desk", desk],
                       capture_output=True, text=True, encoding="utf-8")
    return p.stdout if p.returncode == 0 else ""


def verdict(name, chars):
    caps = CAPS.get(name, {})
    lo, hi = tokens(chars)
    hard = caps.get("hard")
    warn = caps.get("warn")
    if hard is None:
        return "不设上限", lo, hi
    if hi > hard:
        return f"[X] 超硬顶 {hard}", lo, hi
    if warn and hi > warn:
        return f"! 过提示线 {warn}", lo, hi
    return "OK", lo, hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows = []
    for name, text in (
        ("内核", read(CARD)),
        ("口播投影", read(WORK)),
        ("技术规范", read(TECH)),
        ("任务看板", read(DESK)),
        ("日常投影", pack("日常")),
        ("工作投影", pack("工作")),
    ):
        chars = han(text)
        state, lo, hi = verdict("技术规范" if name in ("技术规范",) else
                                (name if name in CAPS else "工作投影"), chars)
        rows.append({"块": name, "汉字": chars, "token_lo": lo, "token_hi": hi,
                     "结论": state if name not in ("口播投影", "任务看板") else "参考"})

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"{'块':<8}{'汉字':>6}   {'token 估':<12}{'结论'}")
        print("-" * 46)
        for r in rows:
            print(f"{r['块']:<8}{r['汉字']:>6}   {r['token_lo']}–{r['token_hi']:<8}{r['结论']}")

    worst = 0
    for r in rows:
        if r["结论"].startswith("[X]"):
            worst = max(worst, 2)
        elif r["结论"].startswith("!"):
            worst = max(worst, 1)
    print(f"\n总判定：{'超硬顶' if worst == 2 else ('过提示线' if worst == 1 else '全部在目标内')}")
    sys.exit(worst)


if __name__ == "__main__":
    for s in (sys.stdout,):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    main()
