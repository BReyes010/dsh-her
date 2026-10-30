#!/usr/bin/env python3
"""her-doctor —— 一条只读诊断：哪里坏了、下一步做什么。

原则：
  · **只读**：不改任何文件、不写库、不签发；
  · 全程副本之外（要判真数据），所以每项都必须是无副作用的检查；
  · 输出人话：每条问题后面跟「下一步」；
  · 退出码：0 全绿 / 1 有警告 / 2 有硬错。

用法：
  python 脚本/her-doctor.py
  python 脚本/her-doctor.py --quiet    # 只打问题和结论
"""

import json
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ["HER_HOME"]).resolve() if _os.environ.get("HER_HOME") else ROOT
SCRIPTS = ROOT / "脚本"
PY = sys.executable

ok_n = warn_n = err_n = 0
issues = []


def ok(msg):
    global ok_n
    ok_n += 1
    print(f"[OK] {msg}")


def warn(msg, next_step=""):
    global warn_n
    warn_n += 1
    print(f"[!]  {msg}")
    if next_step:
        print(f"     下一步：{next_step}")
        issues.append((msg, next_step))


def err(msg, next_step=""):
    global err_n
    err_n += 1
    print(f"[X]  {msg}")
    if next_step:
        print(f"     下一步：{next_step}")
        issues.append((msg, next_step))


def run(script, *args):
    p = subprocess.run([PY, str(SCRIPTS / script), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def sec(t):
    print(f"\n—— {t} ——")


def main():
    quiet = "--quiet" in sys.argv
    print(f"her-doctor｜引擎 {ROOT.name}｜数据 {DATA}")
    if quiet:
        print()

    # 1 运行环境
    sec("1 运行环境")
    if sys.version_info >= (3, 11):
        ok(f"Python {sys.version.split()[0]}")
    else:
        warn(f"Python {sys.version.split()[0]} 偏低（建议 3.11+）", "升级 Python，或换 DSH 自带运行时")
    for m in ("sqlite3", "json", "hashlib"):
        try:
            __import__(m)
        except Exception:
            err(f"缺标准库 {m}", "重装 Python")

    # 2 数据与卡套件
    sec("2 数据与卡套件")
    reg = DATA / "cards" / "_registry.json"
    if reg.exists():
        ok("卡套件注册表存在")
    else:
        err("缺 cards/_registry.json", "跑 python 脚本/new_card.py 或 her_home.py init 重建")
    mdb = DATA / "记忆" / "memory.db"
    if mdb.exists():
        try:
            con = sqlite3.connect(f"file:{mdb}?mode=ro", uri=True)
            n = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            con.close()
            ok(f"L2 库可读（{n} 条）")
        except Exception as e:
            err(f"L2 库打不开：{e}", "备份后跑 memory_db.py init / import")
    else:
        err("缺 记忆/memory.db", "跑 her_home.py init")

    # 3 内核指纹
    sec("3 内核（L0）")
    rc, out, errout = run("l0_guard.py")
    if rc == 0:
        ok("L0 指纹一致")
    else:
        err("L0 指纹不符（人格卡被改过或版本没登记）",
            "看 记忆/L3/L0-changelog.md；确认是提案制改动，否则回滚")

    # 4 卡与装配策略
    sec("4 卡")
    rc, out, errout = run("pack_assembler.py", "list")
    if rc == 0:
        cards = [l for l in out.splitlines() if l.startswith("  ")]
        ok(f"卡目录可解析（{len(cards)} 张）")
        if "kind=task" not in out:
            warn("还没有任务卡", "建一张：skills/<交付名>.md，kind: task")
    else:
        err("卡目录解析失败", "看 脚本/pack_assembler.py list 的报错")

    # 5 包与记忆同版（走 preflight 的只读部分）
    sec("5 启动包")
    rc, out, errout = run("preflight.py", "--mode", "日常", "--no-heartbeat")
    if rc in (0, 1):
        bad = [l for l in out.splitlines() if l.strip().startswith("X") or "X " in l[:4]]
        if bad:
            warn(f"preflight 报了 {len(bad)} 条硬问题", "跑 preflight 看详情")
        else:
            ok("preflight 通过（有提醒属正常）")
    else:
        err("preflight 跑不起来", "看 脚本/preflight.py 的报错")

    # 6 写权
    sec("6 写权")
    rc, out, errout = run("grant.py", "list")
    if rc == 0:
        first = out.splitlines()[0] if out.splitlines() else ""
        if "有效 0 条" in first:
            ok("当前无有效签发（只读；要写先 grant.py sign）")
        else:
            ok(f"签发：{first}")
    else:
        warn("签发文件读不出", "删掉 入口/_签发.json 让脚本重建（会丢活票）")

    # 7 MCP 服务
    sec("7 MCP 服务")
    port = 8790
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            ok(f"127.0.0.1:{port} 在监听")
    except Exception:
        warn(f"127.0.0.1:{port} 没在监听（只影响外部客户端）",
             "双击 入口/启动-Her-MCP.cmd，或查 MCP接入说明.md")
    log = DATA / "入口" / "mcp.log"
    if log.exists() and log.stat().st_size > 2_000_000:
        warn(f"mcp.log 已 {log.stat().st_size // 1024} KB", "删掉或清空 入口/mcp.log")

    # 8 快照
    sec("8 回退能力")
    hist = DATA / "启动包" / "历史"
    snaps = sorted(hist.iterdir()) if hist.exists() else []
    if snaps:
        ok(f"有 {len(snaps)} 份快照（最近 {snaps[-1].name}）")
    else:
        warn("没有任何冻结快照", "python 脚本/snapshot.py freeze --note \"基线\"")

    # 9 L1
    sec("9 L1")
    l1 = None
    try:
        sys.path.insert(0, str(ROOT / "脚本"))
        import paths as _p
        l1 = _p.paths().get("l1")
    except Exception:
        l1 = None
    if l1 is None:
        l1 = DATA / "memory" / "dafeiyu" / "l1.db"
    if l1.exists():
        try:
            con = sqlite3.connect(f"file:{l1}?mode=ro", uri=True)
            act = con.execute("SELECT COUNT(*) FROM l1 WHERE state IN('active','protected')").fetchone()[0]
            cand = con.execute("SELECT COUNT(*) FROM l1 WHERE state='candidate'").fetchone()[0]
            con.close()
            ok(f"L1 活跃 {act}/10｜候选 {cand}")
        except Exception as e:
            warn(f"L1 库读不出：{e}", "看 记忆/L1-规范.md")
    else:
        warn("还没有 L1 库", "python 脚本/l1.py init")

    # 结论
    print("\n" + "=" * 50)
    print(f"通过 {ok_n}｜提醒 {warn_n}｜错误 {err_n}")
    if issues:
        print("\n要处理的（按出现顺序）：")
        for i, (m, s) in enumerate(issues, 1):
            print(f"  {i}. {m}")
            print(f"     → {s}")
    print("结论：", "全绿" if err_n == 0 and warn_n == 0 else
          ("有硬错，先修" if err_n else "可用，但有提醒"))
    return 0 if err_n == 0 and warn_n == 0 else (2 if err_n else 1)


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
