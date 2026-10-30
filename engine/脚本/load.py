#!/usr/bin/env python3
"""load：把一句话变成行动（触发词路由器）。

支持三类话：
  1. 载入角色：  "<卡> <模式>"         例：{角色名} 工作模式 / dafeiyu 完整 / {角色名} 日常
  2. 出差：      "去千问出差吧" / "出差吧"   → 生成出差包（需要 --task）
  3. 查看：      "--list"              → 列出所有卡与模式

解析顺序（卡）：id 全等 → 名字全等 → 名字包含；命中多个 → 报歧义，要求用 id。
解析顺序（模式）：日常 / 工作 / 完整；缺省 = 日常。

用法：
  python 脚本/load.py "{角色名} 工作模式"
  python 脚本/load.py "{角色名} 完整"
  python 脚本/load.py "去千问出差吧" --task "写第 3 期"
  python 脚本/load.py --list
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
HOST = DATA / "入口" / "her-host.py"
MODES = ("日常", "工作", "完整")
TRIP_RE = re.compile(r"出差")


def cards():
    reg = json.loads((DATA / "cards" / "_registry.json").read_text(encoding="utf-8-sig"))
    return reg


def _pick(cands, word):
    """多个候选时的取舍：现役优先；否则报歧义。"""
    if len(cands) == 1:
        return cands[0]["id"], None
    live = [c for c in cands if c.get("kind") == "live"]
    if len(live) == 1:
        return live[0]["id"], None
    return None, "匹配到多张卡，请用 id：" + "、".join(f'{c["id"]}（{c.get("name")}）' for c in cands)


def resolve_card(word, reg):
    """顺序：id 全等 → 别名全等 → 名字全等 → 名字/别名/id 包含；同级多命中时现役优先。"""
    word = word.strip()
    for c in reg["cards"]:
        if c["id"] == word:
            return c["id"], None
    for c in reg["cards"]:
        if word in (c.get("aliases") or []):
            return c["id"], None
    by_name = [c for c in reg["cards"] if c.get("name") == word]
    if by_name:
        return _pick(by_name, word)
    part = [c for c in reg["cards"]
            if word in c.get("name", "") or word in c["id"] or word in (c.get("aliases") or [])]
    if part:
        return _pick(part, word)
    return None, "认不出这张卡。可用：" + "、".join(
        f'{c["id"]}（{c.get("name")}）' for c in reg["cards"])


def resolve_mode(text):
    for m in MODES:
        if m in text:
            return m
    return "日常"


def list_all():
    reg = cards()
    print(f"{'id':<18}{'名字':<22}{'kind':<10}场景")
    print("-" * 72)
    for c in reg["cards"]:
        cj = DATA / "cards" / (c.get("dir") or c["id"]) / "card.json"
        if cj.exists():
            scenes = "、".join(s["id"] for s in json.loads(cj.read_text(encoding="utf-8-sig"))["scenes"])
        else:
            scenes = "（现役：人格/ + 工作/）"
        print(f'{c["id"]:<18}{c.get("name",""):<22}{c.get("kind",""):<10}{scenes}')
    print("\n模式：" + " / ".join(MODES) + "（缺省 日常）")
    print("用法：python 脚本/load.py \"<卡 id 或名字> <模式>\"")


def load_pack(card, mode):
    import os
    env = dict(os.environ)
    env["HER_CARD"] = card
    p = subprocess.run([sys.executable, str(HOST), "--print", mode],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    if p.returncode != 0:
        sys.exit(f"取包失败：{(p.stderr or '')[:300]}")
    return p.stdout


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="触发词路由器")
    ap.add_argument("text", nargs="*", help="用户原话，如：{角色名} 工作模式")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--task", default="", help="出差时的任务说明")
    ap.add_argument("--to", default="外部客户端")
    a = ap.parse_args()

    if a.list or not a.text:
        list_all()
        return

    say = " ".join(a.text).strip()
    reg = cards()

    # ── 出差 ──
    if TRIP_RE.search(say):
        if not a.task:
            print("[需要任务] 这是出差指令，但我不知道去干什么。")
            print(f'  用法：python 脚本/load.py "{say}" --task "写第 3 期口播稿" --to 千问')
            return
        to = a.to
        m = re.search(r"去\s*([^\s，。,]{1,12}?)\s*出差", say)
        if m:
            to = m.group(1)
        # 出差默认「工作」（多半是去干活）；说了模式就用说的
        trip_mode = next((m for m in MODES if m in say), "工作")
        cmd = [sys.executable, str(ROOT / "脚本" / "outbound.py"),
               "--to", to, "--task", a.task, "--mode", trip_mode]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        print((r.stdout or r.stderr).strip())
        return

    # ── 载入角色 ──
    mode = resolve_mode(say)
    word = say
    for m in MODES:
        word = word.replace(m, "")
    word = word.replace("模式", "").strip(" ，,。")
    if not word:
        print("没说哪张卡。可用：")
        list_all()
        return

    cid, err = resolve_card(word, reg)
    if err:
        print("[认不出] " + err)
        return

    pack = load_pack(cid, mode)
    han = sum(1 for c in pack if "\u4e00" <= c <= "\u9fff")
    name = next((c.get("name", cid) for c in reg["cards"] if c["id"] == cid), cid)
    print(f"[卡 {cid}｜{name}｜模式 {mode}｜{han} 汉字]")
    print("=" * 60)
    print(pack)


if __name__ == "__main__":
    main()
