#!/usr/bin/env python3
"""returnflow：出差（外包客户端）交回的「回程」自动落 handoff。

场景：把出差包发给千问/DeepSeek 等外部客户端，它们按最后一节〈回程〉模板交回一段文本。
过去要手动贴进 handoff；现在直接吃回来。

用法：
  python 脚本/returnflow.py intake --file 回程.md --client 千问
  python 脚本/returnflow.py intake --stdin --client 千问 --dry-run
  python 脚本/returnflow.py list

规矩：
  · **一条都不能省**：13 个字段缺一个就拒绝，并列出缺了哪些（不补、不猜）；
  · **原话至少 3 句**：少于 3 句拒绝（原话是唯一不能省的那条）；
  · **幂等**：同一段回程只落一次（`handoff/_回程.json` 记 sha256）；
  · 写进去的是 `handoff.py write_session` 的同一套格式，客户端标成「回程-<client>」。
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
sys.path.insert(0, str(ROOT / "脚本"))

L3 = DATA / "记忆" / "L3"
HANDOFF = L3 / "handoff"
LEDGER = HANDOFF / "_回程.json"

FIELDS = ["主题", "谁做的", "做了什么", "起点", "卡点", "走过的岔路",
          "试过没成的", "取舍", "哪里还没想明白", "原话", "open", "closes", "留下"]
REQUIRED = FIELDS  # 一条都不能省


def read_input(a):
    if a.stdin:
        return sys.stdin.read()
    p = Path(a.file)
    if not p.exists():
        sys.exit(f"文件不存在：{p}")
    return p.read_text(encoding="utf-8-sig", errors="replace")


def extract_block(text):
    """从交回的文本里抠出〈回程〉那段（从第一个「主题：」开始）。"""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*(```)?\s*主题\s*[：:]", line):
            start = i
    if start is None:
        sys.exit("没找到「主题：」——看起来不是回程模板")
    block = "\n".join(lines[start:])
    block = re.sub(r"^\s*```.*$", "", block, flags=re.M)   # 去掉代码围栏
    return block


def parse_fields(block):
    """按「字段：值」解析；值可以换行，直到下一个字段。"""
    out = {}
    cur = None
    for raw in block.splitlines():
        line = raw.rstrip()
        m = re.match(r"^\s*(" + "|".join(map(re.escape, FIELDS)) + r")\s*[：:]\s*(.*)$", line)
        if m:
            cur = m.group(1)
            val = m.group(2).strip()
            # 「原话」天然会出现多次，累加而不是覆盖
            if cur in out and cur == "原话":
                out[cur] = (out[cur] + "\n" + val).strip()
            else:
                out[cur] = val
        elif cur and line.strip():
            out[cur] += "\n" + line.strip()
    return {k: v.strip() for k, v in out.items()}


def validate(data):
    missing = [f for f in REQUIRED if not data.get(f)]
    problems = []
    if missing:
        problems.append("缺字段：" + "、".join(missing))
    quotes = [q for q in re.split(r"[\n；;]+", data.get("原话", "")) if q.strip()]
    if data.get("原话") and len(quotes) < 3:
        problems.append(f"原话只有 {len(quotes)} 句（至少要 3 句）")
    who = data.get("谁做的", "")
    if who and who not in ("她", "你", "助手", "一起", "用户"):
        problems.append(f"谁做的=「{who}」不在 她/你/助手/一起 里")
    return problems


def compose(data):
    """拼成 handoff.py 认的条目格式。"""
    order = ["谁做的", "做了什么", "主题", "起点", "卡点", "走过的岔路",
             "试过没成的", "取舍", "哪里还没想明白", "原话", "open", "closes", "留下"]
    body = ["## 条目 1"]
    for k in order:
        val = data.get(k, '—')
        if k == "原话":
            # 多句原话逐行都带「原话：」，方便 handoff 解析与人工核对
            lines = [q.strip() for q in val.splitlines() if q.strip()]
            body.extend(f"原话：{q}" for q in lines)
        else:
            body.append(f"{k}：{val}")
    return "\n".join(body)


def load_ledger():
    if LEDGER.exists():
        try:
            return json.loads(LEDGER.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_ledger(d):
    LEDGER.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_intake(a):
    text = read_input(a)
    block = extract_block(text)
    data = parse_fields(block)
    problems = validate(data)
    if problems:
        print("[X] 回程不合格，没有落盘：")
        for p in problems:
            print("   -", p)
        print("\n不补、不猜——把这几条补齐再交一次。")
        sys.exit(2)

    sha = hashlib.sha256(re.sub(r"\s+", "", block).encode("utf-8")).hexdigest()[:16]
    ledger = load_ledger()
    body = compose(data)

    if a.dry_run:
        dup = f"（注意：同样内容在 {ledger[sha]['ts']} 已落过 {ledger[sha]['handoff']}）" if sha in ledger else ""
        print(f"[dry-run] 会写成 handoff（客户端 {a.client}）{dup}：\n")
        print(body)
        return

    if sha in ledger:
        print(f"[=] 这段回程已经落过了：{ledger[sha]['handoff']}（{ledger[sha]['ts']}）")
        return

    import handoff as hf
    client = a.client
    hf.write_session(client, body, session=f"{datetime.now():%Y%m%d-%H%M}-回程")
    newest = max(HANDOFF.glob("20*.md"), key=lambda p: p.stat().st_mtime)
    ledger[sha] = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "file": a.file or "(stdin)", "handoff": newest.name}
    save_ledger(ledger)
    print(f"[OK] 回程已落 handoff：{newest.name}")
    print(f"     主题：{data['主题']}｜谁做的：{data['谁做的']}｜原话 {len(re.split(r'[；;\\n]+', data['原话']))} 句")


def cmd_list(a):
    ledger = load_ledger()
    if not ledger:
        print("还没有回程记录")
        return
    for sha, rec in sorted(ledger.items(), key=lambda kv: kv[1]["ts"]):
        print(f"{rec['ts']}  {rec['handoff']}  ← {rec['file']}  [{sha}]")
    print(f"\n共 {len(ledger)} 条")


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="回程自动落 handoff")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("intake")
    p.add_argument("--file")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--client", default="外部")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_intake)

    sub.add_parser("list").set_defaults(fn=cmd_list)
    a = ap.parse_args()
    if a.cmd == "intake" and not a.file and not a.stdin:
        sys.exit("要给 --file 或 --stdin")
    a.fn(a)


if __name__ == "__main__":
    main()
