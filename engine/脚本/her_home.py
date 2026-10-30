#!/usr/bin/env python3
"""her_home：用户数据目录（HER_HOME）的初始化与检查。

为什么要有它：引擎升级会整份替换代码，**用户的卡与记忆必须放在代码外面**，
否则升级一次就把她的记忆冲了。

用法：
  python 脚本/her_home.py check                 # 看当前数据根在哪、目录齐不齐
  python 脚本/her_home.py init --dest D:/her    # 用模板卡初始化一个空的数据根
  HER_HOME=D:/her python 入口/her-host.py --print 日常   # 验证引擎从新数据根读

数据根应当包含：人格/  记忆/  入口/  启动包/  cards/  memory/
"""

import argparse
import json
import os
import subprocess
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ["HER_HOME"]).resolve() if os.environ.get("HER_HOME") else ROOT
TPL_CARD = "dafeiyu-template"

MIN_WORKBENCH = {
    "version": 1, "mode": "dual",
    "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    "client": "初始化",
    "header": "任务看板（跨客户端交接单）",
    "now": "刚建好，还没开始",
    "decisions": [], "blockers": [], "next": [], "pitfalls": [],
    "baseline": {"decisions": 0},
    "md_sha": "",
}


def cmd_check(a):
    print(f"代码根 ROOT：{ROOT}")
    print(f"数据根 DATA：{DATA}{'（HER_HOME）' if DATA != ROOT else '（没设 HER_HOME，退回引擎目录）'}")
    need = ["人格", "记忆", "入口", "启动包", "cards", "memory"]
    bad = [d for d in need if not (DATA / d).exists()]
    for d in need:
        print(f"  {'[OK]' if (DATA / d).exists() else '[X]'} {d}")
    wb = DATA / "入口" / "任务看板.json"
    print(f"  {'[OK]' if wb.exists() else '[X]'} 入口/任务看板.json")
    if bad:
        print(f"\n问题：缺 {len(bad)} 个目录（{', '.join(bad)}）——先跑 her_home.py init")
        sys.exit(1)
    print("\n结论：数据根可用")


def _merge_tree(src, dst):
    """只补缺、不覆盖（单目录安装用：代码目录里已有规范等文件）。"""
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)


def cmd_init(a):
    global TPL_CARD
    if getattr(a, "card", ""):
        TPL_CARD = a.card
    dest = Path(a.dest).resolve()
    single = (dest == ROOT.resolve())      # 单目录安装：代码与数据同一个文件夹
    if dest.exists() and any(dest.iterdir()) and not a.force and not single:
        print(f"[X] 目标非空：{dest}（加 --force 覆盖）")
        sys.exit(2)
    dest.mkdir(parents=True, exist_ok=True)

    # 1) 人格：用模板卡（不带任何私名）；已有就不动
    (dest / "人格").mkdir(exist_ok=True)
    src_persona = ROOT / "cards" / TPL_CARD / "persona.md"
    if not (dest / "人格" / "人格卡.md").exists():
        shutil.copy2(src_persona, dest / "人格" / "人格卡.md")

    # 2) 记忆：模板套件（单目录时只补缺，别把 记忆/L2-规范.md 这些删了）
    if single:
        _merge_tree(ROOT / "memory" / TPL_CARD, dest / "记忆")
    else:
        if (dest / "记忆").exists():
            shutil.rmtree(dest / "记忆")
        shutil.copytree(ROOT / "memory" / TPL_CARD, dest / "记忆")

    # 3) cards / memory：套件整份复制，注册表**照抄模板条目**
    for d, key in (("cards", "cards"), ("memory", "kits")):
        (dest / d).mkdir(exist_ok=True)
        marker_name = "card.json" if d == "cards" else "kit.json"

        # ① 复制所有 template 目录（不只默认那张；_template 是骨架，跳过）
        for cand in sorted((ROOT / d).iterdir()):
            if not cand.is_dir() or cand.name.startswith("_"):
                continue
            if not (cand / marker_name).exists():
                continue
            _merge_tree(cand, dest / d / cand.name)

        # ② 注册表按"盘上有什么"来建（和用户看到的完全一致）
        regs = []
        for cand in sorted((dest / d).iterdir()):
            if not cand.is_dir() or cand.name.startswith("_"):
                continue
            mf = cand / marker_name
            if not mf.exists():
                continue
            try:
                meta = json.loads(mf.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            cid = meta.get("id") or cand.name
            if "{" in str(cid):
                continue                      # 占位卡不发
            if d == "cards":
                regs.append({
                    "id": cid, "name": meta.get("name", cid),
                    "kind": meta.get("kind", "template"),
                    "memory_id": meta.get("memory_id", cid),
                    "dir": cand.name, "scenes": meta.get("scenes", []),
                    "version": meta.get("version", ""),
                })
            else:
                regs.append({
                    "id": cid, "name": meta.get("name", cid),
                    "kind": meta.get("kind", "template"), "dir": cand.name,
                })

        if not regs:
            print(f"[warn] {d} 下没有可注册的条目")

        if d == "cards":
            out = {"default_card": TPL_CARD, "cards": regs}
        else:
            out = {"default_card": TPL_CARD, "kits": regs}
        (dest / d / "_registry.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4) 入口 / 启动包
    (dest / "入口").mkdir(exist_ok=True)
    (dest / "入口" / "任务看板.json").write_text(
        json.dumps(MIN_WORKBENCH, ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "启动包").mkdir(exist_ok=True)

    # 5) 运行期目录（2026-10-29 加：空工作区验收暴露缺这些）
    for sub in ("记忆/L3/handoff", "记忆/L3/任务", "记忆/L3/任务卡归档",
                "记忆/L3/归档", "提案", "备份"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    arc = dest / "记忆" / "L3" / "归档" / "任务看板-已作废.md"
    if not arc.exists():
        arc.write_text("# 任务看板 · 已作废（归档）\n\n> 退休条目自动追加到这里。\n",
                       encoding="utf-8")
    if not (dest / "记忆" / "L3" / "归档" / "_归档.json").exists():
        import hashlib as _hl
        sha = _hl.sha256(arc.read_bytes()).hexdigest()
        (dest / "记忆" / "L3" / "归档" / "_归档.json").write_text(
            json.dumps({"count": 0, "ids": [], "sha256": sha}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    # 6) 生成出差包 + 预设 + 检索索引（不生成的话 preflight 会报"缺 _meta.json"）
    env2 = dict(os.environ, HER_HOME=str(dest), PYTHONIOENCODING="utf-8",
                HER_CARD=TPL_CARD)
    for script, args in (("l1.py", ["init"]),
                         ("make_mode_pack.py", []), ("workbench.py", ["render"]),
                         ("l3_index.py", ["build"])):
        sp = ROOT / "脚本" / script
        if sp.exists():
            r = subprocess.run([sys.executable, str(sp), *args], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", env=env2)
            if r.returncode != 0:
                print(f"[!] {script} 没跑成功：{(r.stderr or r.stdout or '')[:120]}")

    # 首份快照（回退基线）
    snap = ROOT / "脚本" / "snapshot.py"
    if snap.exists():
        subprocess.run([sys.executable, str(snap), "freeze", "--note", "初始化基线"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env2)

    print(f"[OK] 数据根已初始化：{dest}")
    print("     已生成：人格卡（模板）/ 记忆库 / 卡注册表 / 任务看板 / 出差包 + 预设 / 检索索引")
    print("     下一步：")
    print(f"       $env:HER_HOME = \"{dest}\"")
    print("       python 脚本/her_home.py check")
    print("       python 脚本/her-doctor.py            # 一条命令看哪里不对")
    print("       python 入口/her-host.py --print 日常   # 应能出包")


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="用户数据目录（HER_HOME）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    p = sub.add_parser("init")
    p.add_argument("--dest", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--card", default="", help="用哪张模板卡初始化（默认 dafeiyu-template；可填 blank）")
    p.set_defaults(fn=cmd_init)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
