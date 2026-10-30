#!/usr/bin/env python3
"""l0_guard：防"未登记的 L0 变更"。

比对两件事：
  1. `人格/人格卡.md` 的 sha256 与 `cards/_registry.json` 里登记的指纹
  2. 卡头版本号与 registry 里登记的版本

结论三档：
  [OK] OK            —— 指纹一致
  ! 有提案在等     —— 指纹不一致，但存在 人格卡.proposed.md（合法流程）
  [X] 未登记的变更   —— 指纹不一致且没有提案（有人直接改了卡）

用法：python l0_guard.py [--json]
退出码：0 OK / 1 有提案 / 2 未登记变更
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
CARD = DATA / "人格" / "人格卡.md"
PROPOSED = DATA / "人格" / "人格卡.proposed.md"
REG = DATA / "cards" / "_registry.json"
CHANGELOG = DATA / "记忆" / "L3" / "L0-changelog.md"
VER_RE = re.compile(r"# 人格卡（(v[0-9.]+)")


def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def han(t):
    return sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    text = CARD.read_text(encoding="utf-8")
    cur_ver = (VER_RE.search(text).group(1) if VER_RE.search(text) else "?")
    cur_sha = sha(text)

    reg = json.loads(REG.read_text(encoding="utf-8-sig")) if REG.exists() else {"cards": []}
    live = next((c for c in reg.get("cards", []) if c.get("id") == "dafeiyu"), {})
    reg_sha = live.get("persona_sha256")
    reg_ver = live.get("persona_version")

    state, code = "OK", 0
    detail = "指纹一致"
    if reg_sha and cur_sha != reg_sha:
        if PROPOSED.exists():
            state, code = "有提案在等", 1
            detail = f"正本已被改动（{reg_ver}→{cur_ver}），且存在提案文件（合法流程）"
        else:
            state, code = "未登记的变更", 2
            detail = f"正本 sha 与登记值不符（{reg_ver}→{cur_ver}），且没有提案文件"
        log = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else ""
        if f"人格卡 {cur_ver}" not in log:
            detail += "；changelog 里也没有该版本行"

    out = {
        "state": state, "card_version": cur_ver, "registry_version": reg_ver,
        "card_sha": cur_sha[:12], "registry_sha": (reg_sha or "")[:12],
        "han": han(text), "detail": detail, "proposal": PROPOSED.exists(),
    }
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        mark = {"OK": "[OK]", "有提案在等": "!", "未登记的变更": "[X]"}[state]
        print(f"{mark} L0 守卫：{state}")
        print(f"  版本：正本 {cur_ver} ／ 登记 {reg_ver}")
        print(f"  指纹：正本 {cur_sha[:12]} ／ 登记 {(reg_sha or '—')[:12]}")
        print(f"  汉字：{han(text)}　{detail}")
        try:
            probe = CARD.parent / ".write-probe"
            probe.write_text("x", encoding="utf-8"); probe.unlink()
            print("  [!] 物理保护未生效：当前用户对「人格」目录可写（跑 脚本/protect_l0.ps1 可锁成仅管理员）")
        except Exception:
            print("  [OK] 物理保护生效：当前用户对「人格」目录只读（改卡须提权）")
        if state != "OK":
            print("  → 处理：")
            print("     合法：把改动放进 人格/人格卡.proposed.md，再跑 l0_apply.py --accept --reason '…'")
            print("     纠正：python 脚本/l0_apply.py --status 看提案；或从 人格/备份/ 恢复旧版")
    sys.exit(code)


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    main()
