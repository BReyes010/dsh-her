#!/usr/bin/env python3
"""wb_query：查已归档的任务看板条目（编号 / 关键词 / 替代链），五秒内返回。

用法：
  python 脚本/wb_query.py D-10          # 按编号
  python 脚本/wb_query.py 人格卡         # 按关键词
  python 脚本/wb_query.py D-10 --chain  # 按替代链（往前找谁取代了它）
  python 脚本/wb_query.py --list        # 列出全部（只出索引行）
"""

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
ARC = DATA / "记忆" / "L3" / "归档" / "任务看板-已作废.md"
META = DATA / "记忆" / "L3" / "归档" / "_归档.json"
JS = DATA / "入口" / "任务看板.json"


def entries():
    """把归档文件拆成 {id: {原文, 理由, 日期, 替代}}。"""
    if not ARC.exists():
        return {}
    text = ARC.read_text(encoding="utf-8")
    out = {}
    for block in re.split(r"^### ", text, flags=re.M)[1:]:
        lines = block.splitlines()
        eid = lines[0].strip()
        rec = {}
        for ln in lines[1:]:
            m = re.match(r"^- (原文|理由|日期|替代)：(.*)$", ln.strip())
            if m:
                rec[m.group(1)] = m.group(2).strip()
        out[eid] = rec
    return out


def fmt(eid, rec, tag=""):
    return (f"{eid}{tag}\n"
            f"  原文：{rec.get('原文','')}\n"
            f"  理由：{rec.get('理由','')}\n"
            f"  日期：{rec.get('日期','')}\n"
            f"  替代：{rec.get('替代','')}")


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    t0 = time.time()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    chain = "--chain" in sys.argv
    listing = "--list" in sys.argv
    data = entries()

    if not data:
        print("没有归档文件（跑 python 脚本/wb_archive.py 生成）")
        return

    if listing:
        for eid in sorted(data):
            print(f"{eid}｜{data[eid].get('原文','')[:40]}｜替代 {data[eid].get('替代','')}")
        print(f"\n共 {len(data)} 条（{time.time()-t0:.2f}s）")
        return

    if not args:
        print(__doc__)
        return

    q = args[0]
    hits = []
    if re.fullmatch(r"D-\d+", q.upper()):
        q = q.upper()
        if q in data:
            hits = [(q, data[q])]
    else:
        for eid, rec in data.items():
            blob = " ".join(rec.values())
            if q.lower() in blob.lower():
                hits.append((eid, rec))

    if not hits:
        print(f"没查到「{q}」（{time.time()-t0:.2f}s）")
        return

    for eid, rec in hits:
        print(fmt(eid, rec))
        if chain:
            # 替代链：顺着 替代 往后找，再回 json 看现在是谁在管
            seen, cur = {eid}, rec.get("替代", "")
            while cur and cur not in seen and re.fullmatch(r"D-\d+", cur):
                seen.add(cur)
                nxt = data.get(cur)
                if not nxt:
                    break
                print(f"  -> {cur}：{nxt.get('原文','')[:40]}")
                cur = nxt.get("替代", "")
            if JS.exists():
                d = json.loads(JS.read_text(encoding="utf-8"))
                alive = [x for x in d.get("decisions", []) if x.get("status") == "active"]
                print(f"  → 现在生效的条目：{', '.join(x['id'] for x in alive)}")
    print(f"\n（{time.time()-t0:.2f}s）")


if __name__ == "__main__":
    main()
