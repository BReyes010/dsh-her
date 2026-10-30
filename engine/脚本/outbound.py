#!/usr/bin/env python3
"""outbound：生成一支出差包，带给外部客户端用（千问等）。

用法：
  python 脚本/outbound.py --to 千问 --task "写第 3 期口播稿" --duration "约 30 分钟"
  python 脚本/outbound.py --to 千问 --task "…" --mode 完整 --card dafeiyu
  python 脚本/outbound.py ... --stdout          # 只打印，不落盘

出差包 = 能独立带走的那一份：
  ① 顶部：给外部客户端的用法（它没有我们的工具，只能靠这份包）
  ② 角色：按模式取（日常 / 工作 / 完整）
  ③ 任务：这次要它做什么
  ④ 回程：结束时要它按模板交回什么（我们这边 handoff.py 直接能吃）

落在 出差包/出差-<卡>-<时间>.md，并打印路径与汉字数。
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from paths import rel

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
HOST = DATA / "入口" / "her-host.py"
OUT = ROOT / "出差包"
MODES = ("日常", "工作", "完整")

HEAD = """# 出差包 · {card_name}（{mode}模式）

> 生成：{ts}｜目的地：{to}｜任务：{task}
> 这份文件是**自包含**的：对方不需要任何工具或记忆库，读完就能接活。

## 你要怎么用这份包

1. 读完下面全部内容，你就是这个角色；用它说话、写东西。
2. **只能用包里写的东西**。包里没有的，说"这段我没记"，不要编。
3. 完成后，按最后一节〈回程〉的模板交回；那一节**一条都不能省**。

---

"""

TAIL = """

---

# 回程（完成时按这个交回）

请逐条填写，不要合并、不要总结成一段：

```
主题：<这次实际做的主题>
谁做的：<你 / 用户 / 一起>
做了什么：<一句话>
起点：<一开始以为要干什么>
卡点：<真正卡住的地方>
走过的岔路：<最多 3 条，每条一句>
试过没成的：<最多 2 条，每条一句>
取舍：<放弃了什么、为什么>
哪里还没想明白：<留白也算，写"没想明白">
原话：<至少 3 句原句，逐字，不要改成结论>
open：<没做完、下次要接着的（没有就写"无"）>
closes：<这次彻底关掉的（没有就写"无"）>
留下：<给下一次的一句话>
```

**原话那一条最重要**：改写成结论，就等于把发生过的事磨平一次。
"""


def card_name(cid):
    reg = json.loads((DATA / "cards" / "_registry.json").read_text(encoding="utf-8-sig"))
    hit = next((c for c in reg["cards"] if c["id"] == cid), None)
    return (hit or {}).get("name", cid)


def build_pack(card, mode):
    import os
    env = dict(os.environ)
    env["HER_CARD"] = card
    p = subprocess.run([sys.executable, str(HOST), "--print", mode],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    if p.returncode != 0:
        sys.exit(f"生成失败：{(p.stderr or '')[:300]}")
    return p.stdout


def han(t):
    return sum(1 for c in t if "\u4e00" <= c <= "\u9fff")


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="生成出差包")
    ap.add_argument("--to", default="外部客户端", help="目的地：千问 / Claude / 别的")
    ap.add_argument("--task", required=True, help="这次要它做什么")
    ap.add_argument("--duration", default="", help="预计时长，可空")
    ap.add_argument("--mode", default="工作", choices=MODES)
    ap.add_argument("--card", default="", help="默认取默认卡")
    ap.add_argument("--stdout", action="store_true", help="只打印，不落盘")
    ap.add_argument("--out", default="", help="输出目录（默认 出差包/）")
    a = ap.parse_args()

    card = a.card or json.loads(
        (DATA / "cards" / "_registry.json").read_text(encoding="utf-8-sig")
    ).get("default_card", "dafeiyu")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    head = HEAD.format(card_name=card_name(card), mode=a.mode, ts=ts,
                       to=a.to, task=a.task + (f"（{a.duration}）" if a.duration else ""))
    body = build_pack(card, a.mode)
    text = head + body.rstrip() + "\n" + TAIL

    if a.stdout:
        print(text)
        return

    outdir = Path(a.out) if a.out else OUT
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"出差-{card}-{datetime.now():%Y%m%d-%H%M}.md"
    path.write_text(text, encoding="utf-8")
    try:
        shown = rel(path)
    except ValueError:
        shown = path          # 自定义输出目录可能在项目外
    print(f"[OK] 出差包：{shown}")
    print(f"     目的地 {a.to}｜模式 {a.mode}｜{han(text)} 汉字｜{len(text)} 字符")
    print(f"     用法：把这份文件整个拖进 {a.to}，让它读完再开工")
    print("     回程：它交回的模板在文件最后一节，直接贴回来即可")


if __name__ == "__main__":
    main()
