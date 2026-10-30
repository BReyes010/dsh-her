#!/usr/bin/env python3
"""l0_apply：人格卡的**提案制**入口。

规矩：agent 不许直接改 `人格/人格卡.md`。它只能写 `人格/人格卡.proposed.md`；
maker 一句“收”，由本脚本执行：备份 → 落盘 → 记 changelog → 更新指纹 → 删提案。

用法：
  python l0_apply.py --status                 # 有没有待处理的提案
  python l0_apply.py --accept --reason "…"    # 接受提案（会备份 + 记 changelog + 算汉字数）
  python l0_apply.py --accept --reason "…" --file 别的提案.md
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from paths import rel

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
CARD = DATA / "人格" / "人格卡.md"
PROPOSED = DATA / "人格" / "人格卡.proposed.md"
BAK = ROOT / "备份" / "L0"   # 放锁目录外：备份不需要提权
CHANGELOG = DATA / "记忆" / "L3" / "L0-changelog.md"
REG = DATA / "cards" / "_registry.json"
VER_RE = re.compile(r"# 人格卡（(v[0-9.]+)")


def han(t):
    return sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")


def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def version_of(text):
    m = VER_RE.search(text)
    return m.group(1) if m else "?"


def update_registry(text):
    reg = json.loads(REG.read_text(encoding="utf-8-sig")) if REG.exists() else {"cards": []}
    for c in reg.get("cards", []):
        if c.get("id") == "dafeiyu":
            c["persona_version"] = version_of(text)
            c["persona_sha256"] = sha(text)
    REG.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def writable(d):
    try:
        probe = Path(d) / ".write-probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="人格卡提案制")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--accept", action="store_true")
    ap.add_argument("--reason")
    ap.add_argument("--file")
    a = ap.parse_args()

    src = Path(a.file) if a.file else PROPOSED
    if a.status or not a.accept:
        print(f"当前正本：{rel(CARD)}（{version_of(CARD.read_text(encoding='utf-8'))}，{han(CARD.read_text(encoding='utf-8'))} 汉字）")
        if src.exists():
            t = src.read_text(encoding="utf-8")
            print(f"待处理提案：{src.name}（{version_of(t)}，{han(t)} 汉字）→ 用 --accept --reason 接受")
        else:
            print(f"没有待处理提案（约定路径：{src.name}）")
        return

    if not src.exists():
        sys.exit(f"没有提案文件：{src}")
    if not a.reason:
        sys.exit("--accept 必须带 --reason（会写进 changelog）")

    if not writable(CARD.parent):
        sys.exit("「人格」目录是只读的（物理保护已启用）：请在**管理员** PowerShell 里运行本脚本。\n"
                 "  临时解锁：powershell -File 脚本/protect_l0.ps1 -Revert（用完记得锁回去）")

    new = src.read_text(encoding="utf-8")
    old = CARD.read_text(encoding="utf-8")
    v_old, v_new = version_of(old), version_of(new)
    if v_new == v_old or v_new == "?":
        sys.exit(f"版本号无效：旧 {v_old} / 新 {v_new}（必须 +1）")

    BAK.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    bak = BAK / f"人格卡.{v_old}.bak-{stamp}.md"
    shutil.copy2(CARD, bak)
    CARD.write_text(new, encoding="utf-8")

    n, lo, hi = han(new), round(han(new) * 1.5), round(han(new) * 2.0)
    row = f"| 人格卡 {v_new} | {datetime.now():%Y-%m-%d} | {a.reason} | {n} | {lo}–{hi} |\n"
    txt = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else \
        "# L0 变更记录\n\n| 版本 | 日期 | 理由 | 汉字 | 估算 token |\n|---|---|---|---|---|\n"
    if f"人格卡 {v_new}" not in txt:
        CHANGELOG.write_text(txt.rstrip("\n") + "\n" + row, encoding="utf-8")

    update_registry(new)
    src.unlink()
    print(f"[OK] {v_old} → {v_new}（{n} 汉字，约 {lo}–{hi} token）")
    print(f"[OK] 备份：{rel(bak)}")
    print(f"[OK] changelog 已记；指纹已更新；提案已删除")


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    main()
