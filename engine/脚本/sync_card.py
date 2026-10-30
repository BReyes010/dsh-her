#!/usr/bin/env python3
"""sync_card —— 卡是真源，投影是派生物（解决双源）。

```
skills/口播.md                    ← 唯一真源（带 part 分区）
  ├─ part: tone-samples  → 工作/口播投影.md      （语气样本 + 说话方式）
  └─ part: rules         → 工作/口播技术规范.md  （技术条款）
```

用法：
  python 脚本/sync_card.py check     # 只比对，不写（不一致退出码 1）
  python 脚本/sync_card.py apply     # 按卡重写两份派生文件
"""

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARD = ROOT / "skills" / "口播.md"
SPEAK = ROOT / "工作" / "口播投影.md"
TECH = ROOT / "工作" / "口播技术规范.md"

MARK = "<!-- 由 skills/口播.md 生成，勿手改；改卡后跑 脚本/sync_card.py apply -->"


def split_card():
    raw = CARD.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
    body = m.group(2) if m else raw
    parts = {}
    cur = None
    buf = []
    for line in body.splitlines():
        mm = re.match(r"<!--\s*part:\s*([\w\-]+)\s*-->", line)
        if mm:
            if cur:
                parts[cur] = "\n".join(buf).strip()
            cur = mm.group(1)
            buf = []
            continue
        if cur:
            buf.append(line)
    if cur:
        parts[cur] = "\n".join(buf).strip()
    return parts, hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def render(parts):
    speak = parts.get("tone-samples", "").strip()
    tech = parts.get("rules", "").strip()
    # 去掉二级标题（原件自己带标题，保留正文与标题层级即可）
    s = f"{MARK}\n\n{speak}\n"
    t = f"{MARK}\n\n{tech}\n"
    return s, t


def norm(t):
    return t.replace("\r\n", "\n").strip()


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    if not CARD.exists():
        print(f"[FAIL] 没有卡：{CARD}")
        return 2
    parts, h = split_card()
    if "tone-samples" not in parts:
        print("[FAIL] 卡里缺 part: tone-samples")
        return 2
    s_text, t_text = render(parts)

    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    cur_s = SPEAK.read_text(encoding="utf-8") if SPEAK.exists() else ""
    cur_t = TECH.read_text(encoding="utf-8") if TECH.exists() else ""
    same = norm(cur_s) == norm(s_text) and norm(cur_t) == norm(t_text)

    if cmd == "check":
        print(f"[{'OK' if same else 'DIFF'}] 卡@{h}｜口播投影 {len(cur_s)}B｜技术规范 {len(cur_t)}B")
        if not same:
            print("  投影与卡不一致 → 跑 脚本/sync_card.py apply（卡是真源）")
        return 0 if same else 1

    if cmd == "apply":
        SPEAK.write_text(s_text, encoding="utf-8")
        TECH.write_text(t_text, encoding="utf-8")
        print(f"[已同步] 卡@{h} → 口播投影 {len(s_text)}B / 技术规范 {len(t_text)}B")
        return 0

    print("用法：sync_card.py check|apply")
    return 2


if __name__ == "__main__":
    sys.exit(main())
