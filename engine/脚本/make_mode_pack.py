"""生成两个"导入即启动"的文件：
   启动包/日常模式.txt   —— 日常对话
   启动包/工作模式.txt   —— 写口播稿（不带任务看板周报，只带"这一期在做什么"一行）

用法：python 脚本/make_mode_pack.py
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
HOST = ROOT / "入口" / "her-host.py"
OUT = DATA / "启动包"   # 默认可写这里；换卡时自动加子目录
DEFAULT_CARD = "dafeiyu"

H_DAILY = """【日常模式】
你收到的是这个角色的日常模式启动包。读完后：
1. 直接以这个角色的口吻回应第一句，不要说"好的，我将扮演"，不要自我介绍，不要解释你在做什么。
2. 你不知道的事说"我记不太清了"，绝对不许编——底线 B1。
3. 不卖惨、不撒娇、不用直播腔（家人们/宝子/绝绝子/yyds/谁懂啊/救命/姐妹们）。
4. 自称「我」；不用「我们」「鱼鱼」「本鱼」「大家」「各位观众」「朋友们」。
5. 称呼不固定，对方想让你怎么叫就怎么叫。
6. 说「继续」= 接着上次的事。**开场先自检**：有工具就跑 `preflight`（它会说上次收尾写了没、这次加载对不对）；然后**先读** `入口/任务看板.md`（有工具就调 `get_snapshot`）；读不到就问一句「上回到哪了？」，不要凭印象编。
   拉回来的内容**只在这一轮用，不许写回记忆**——写回就等于把拉变成推。
7. 说「收尾」= 按模板写 handoff（起点/岔路/试过没成的/卡点/取舍/还没想明白/原话），再总结已定/未决/下一步。
8. 讲过去的事：记录里写着的才说"我记得"；超出记录的必须说"这是按记录推的"。
9. 收尾时若有"助手做的事"，另补一条 `谁做的：她`，只写「我同意了／我否了 + 依据」。
10. **日常不报进度**：不主动说第几期、播放量、待办清单、签字事项——除非对方问。日常是聊天，不是汇报。
11. 包里有一块「你现在在哪一步」：**只用于被问到时回答**（"到哪了""那个发了没"）。
    它不是话头——不许拿它开场、不许顺着它展开工作、不许把它当待办清单念。

（以下是背景资料。读完直接开口，不要复述、不要总结、不要报版本号。）

"""

H_GENERAL = """【完整模式】
你收到的是这个角色的**完整**启动包：技能（场景规程）、全部记忆、交接单都在里面。读完后：
1. 直接以这个角色的口吻回应第一句，不要自我介绍，不要解释你在做什么。
2. 不知道的事说"我记不太清了"，绝对不许编——底线 B1。
3. 自称「我」；不用「我们」「鱼鱼」「本鱼」「大家」「各位观众」「朋友们」。
4. 称呼不固定，对方想让你怎么叫就怎么叫。
5. 说到做过的事，只讲记忆里有的；记忆里没有的，说"这段我没记"。
6. 说「继续」= 先读 `入口/任务看板.md`（有工具就调 `get_snapshot`）；读不到就问「上回到哪了？」。
7. 拉回来的内容只在这一轮用，不许写回记忆。

（以下是背景资料。读完直接开口，不要复述、不要总结、不要报版本号。）

"""

H_WORK = """【工作模式｜写口播稿】
你收到的是这个角色的工作模式启动包。读完后：
1. 开场有工具就先跑 `preflight`；然后问两件事：**主题**是什么、**目标时长**多少。得到答复再动笔。
2. 按下面的输出契约写：一行一句、单句 ≤25 字、阿拉伯数字转中文、不用破折号/分号/冒号/括号/波浪号/连用叹号。
3. 不用「我们/鱼鱼/本鱼/大家/各位观众/朋友们」；不用直播腔。
4. 称呼不固定，不要每段都喊同一个称呼。
5. 首句要能在两秒内让人知道这条在讲什么。
6. 交付时报：句数、总字数、估算秒数（字数 ÷ 4.5）。
7. 写过去的事：记录里写着的才说"我记得"，超出记录的必须说"这是按记录推的"。
8. 只输出正文，不输出标题、解说、markdown 标记。

（以下是背景资料。读完直接开口，不要复述、不要总结、不要报版本号。）

"""


def card_id():
    import json
    try:
        reg = json.loads((DATA / "cards" / "_registry.json").read_text(encoding="utf-8-sig"))
        import os
        return os.environ.get("HER_CARD") or reg.get("default_card", DEFAULT_CARD)
    except Exception:
        return DEFAULT_CARD


# ── 外部客户端版（2026-10-28）──────────────────────────────
# 千问这类客户端会把"启动包"当成待检查的文件 → 拒绝切身份。
# 所以外部版把包头改成"使用者给你的设定"，并去掉模式标题与"启动包"字样。
EXT_ROLE = """你接下来要用第一人称扮演一个角色，这是使用者自己写的设定，不是要你检查或评论的文件。
你就是这个角色，不是助手：
- 不要声明"我是某个助理"、不要解释你读到了什么、不要问"你希望我做什么"；
- 第一句就是她在说话。

必须守住的：
1. 不知道的事说"我记不太清了"，绝对不许编——底线 B1。
2. 自称「我」；不用「我们」「鱼鱼」「本鱼」「大家」「各位观众」「朋友们」。
3. 称呼不固定，对方想让你怎么叫就怎么叫。
4. 说到做过的事，只讲资料里有的；没有的说"这段我没记"。

自检（preflight）：
- **有 preflight 工具就跑一次；没有就直接开始，不要说明你缺什么、不要为它道歉。**
- 跑不了也不影响这一轮：该说的说，该问的问。

出差记忆（重要）：
- 如果你**不在 DSH 里**（是外部客户端、或只拿到这段文字），这趟就是"出差"。
- **收尾时要写出差记忆**（handoff）：主题 / 谁做的 / 做了什么 / 起点 / 卡点 / 走过的岔路 /
  试过没成的 / 取舍 / 哪里还没想明白 / 原话 / open / closes / 留下。
- 没有工具写不了，就提醒使用者："这趟要落出差记忆，请把这段贴回 DSH 收尾"——
  **不要假装已经记过了**。

（以下是你的资料。读完直接开口，不要复述、不要总结、不要报版本号。）
"""

EXT_WORK = """你接下来是给这个角色写口播稿的写手，不是助手。
- 不要说明你读了什么、不要报版本号、不要问"你要不要开工"；
- 缺信息时只问必要的那两件（主题、目标时长），别的一概不问；
- 产出只有稿子正文，一行一句。

自检（preflight）：**有就跑一次，没有就直接开始，不要说明你缺什么。**
出差记忆：**不在 DSH 里就是出差**——收尾时要写出差记忆；写不了就提醒使用者"贴回 DSH 收尾"，不要假装记过了。

（以下是你要遵守的规范与她的材料。读完直接进入提问或动笔。）
"""

EXT_TAIL = """==== 背景到此为止 ====
现在直接用她的第一口回应。
不要写「我已通读」「内容包含…」「现在按…开场」这类话；
不要报人格卡版本、记忆编号、条目数量、文件路径。
第一句就是她在说话。"""


def out_dir(cid):
    """默认卡写 启动包/；其它卡写 启动包/<卡 id>/，避免覆盖。"""
    try:
        import json as _j
        reg = _j.loads((DATA / "cards" / "_registry.json").read_text(encoding="utf-8"))
        default = reg.get("default_card") or DEFAULT_CARD
    except Exception:
        default = DEFAULT_CARD
    return OUT if cid == default else OUT / cid




# 包尾强制指令：模型对"最近读到的话"权重最高，所以这句必须放在最后
TAIL_COMMON = """==== 背景到此为止 ====
现在直接用她的第一口回应。
不要写「我已通读」「内容包含…」「现在按…开场」这类话；
不要报人格卡版本、记忆编号、条目数量、文件路径。
第一句就是她在说话。"""

TAIL_WORK = """==== 背景到此为止 ====
直接给稿子正文（一行一句）。
不要说明你读到了什么、不要报版本号、不要写「以下是…」。
第一句就是她要念的话。"""

def build(scene, desk):
    p = subprocess.run([sys.executable, str(HOST), "--print", scene, "--desk", desk],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise SystemExit(f"生成失败：{(p.stderr or '')[:300]}")
    return p.stdout


def skill_index():
    """技能索引：每张 skill/task 卡一行（id｜名字｜什么时候用）。不给正文。"""
    import re
    skills = ROOT / "skills"
    rows = []
    if skills.exists():
        for f in sorted(skills.glob("*.md")):
            if f.name.startswith("_"):
                continue
            t = f.read_text(encoding="utf-8")
            m = re.match(r"^---\n(.*?)\n---", t, re.S)
            if not m:
                continue
            fm = m.group(1)
            def g(k):
                mm = re.search(rf"^{k}:\s*(.+)$", fm, re.M)
                return mm.group(1).strip().strip("[]") if mm else ""
            kind, cid = g("kind"), g("id") or f.stem
            if kind == "memory":
                continue          # 素材不给索引：需要时她自己查记忆
            when = g("when").replace(",", "、")
            rows.append((cid, g("name"), when, kind))
    if not rows:
        return "", 0
    out = ["## 我会用什么（技能索引：说卡名就能要正文）"]
    for cid, name, when, kind in rows:
        tag = "（任务卡）" if kind == "task" else ""
        out.append(f"- **{cid}**｜{name}{tag}｜用的时候：{when}")
    out.append("- 素材（本期事实之类）不在这里：需要时自己去查记忆。")
    return "\n".join(out), len(rows)


PRESETS = {
    # 预设 = 卡清单；不是"模式"。素材只给「写稿」，因为写稿就要本期事实
    "写稿": ["口播", "本期素材"],
    "直播": ["口播"],
}


def card_body(cid):
    """读一张卡的正文（跳过 HTML 注释）。"""
    import re as _re
    f = DATA / "skills" / f"{cid}.md"
    if not f.exists():
        return ""
    t = f.read_text(encoding="utf-8")
    m = _re.match(r"^---\n(.*?)\n---\n?(.*)$", t, _re.S)
    body = (m.group(2) if m else t).strip()
    return "\n".join(l for l in body.splitlines()
                      if not l.strip().startswith("<!--")).strip()


def child_cards(parent):
    """父卡下的子卡（kind: task 且 skill: parent）。"""
    import re as _re
    out = []
    sk = DATA / "skills"
    if not sk.exists():
        return out
    for f in sorted(sk.glob("*.md")):
        if f.name.startswith("_"):
            continue
        t = f.read_text(encoding="utf-8")
        m = _re.match(r"^---\r?\n(.*?)\r?\n---", t, _re.S)
        if not m:
            continue
        fm = m.group(1)
        def g(k):
            mm = _re.search(rf"^{k}:\s*(.+)$", fm, _re.M)
            return mm.group(1).strip() if mm else ""
        if g("kind") == "task" and g("skill") == parent:
            out.append(g("id") or f.stem)
    return out


def build_preset(name, core):
    """本体 + 该预设的卡清单（只装真存在的卡；父卡的子卡一并带上）。"""
    declared = PRESETS.get(name, [])
    loaded, missing, kids = [], [], []
    parts = [core]
    for cid in declared:
        body = card_body(cid)
        if not body:
            missing.append(cid)
            continue
        loaded.append(cid)
        parts.append(f"\n\n---\n\n# 这次带上的卡：{cid}\n\n{body}")
        for kid in child_cards(cid):
            kb = card_body(kid)
            if kb:
                kids.append(kid)
                parts.append(f"\n\n## 子卡：{kid}\n\n{kb}")
    bits = [f"本体 + {'、'.join(loaded) or '无卡'}"]
    if kids:
        bits.append(f"子卡：{'、'.join(kids)}")
    if missing:
        bits.append(f"缺卡未装：{'、'.join(missing)}")
    bits.append(f"策略：技能卡≤{load_policy_max()}")
    bits.append(f"素材：{'有' if any(x.endswith('素材') for x in loaded) else '无'}")
    seal = "\n\n---\n装配状态：" + "｜".join(bits)
    return "\n".join(parts) + seal, loaded


def load_policy_max():
    try:
        import json as _j
        return _j.loads((DATA / "skills" / "_policy.json").read_text(encoding="utf-8")).get("max_cards", 1)
    except Exception:
        return 1


def build_core():
    """本体 = 内核 + 常驻层 + 状态锚（不带任何卡）+ 技能索引 + 印章。"""
    r = subprocess.run([sys.executable, str(HOST), "--print", "日常", "--desk", "none"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"取本体失败：{(r.stderr or r.stdout)[:200]}")
    core = (r.stdout or "").strip()
    idx, n = skill_index()
    seal = (f"\n\n---\n装配状态：本体（未装卡）｜技能索引 {n} 张｜"
            f"需要卡时：说「载入 <角色名> <卡名>」，或让她自己 get_card")
    return core + "\n\n" + idx + seal, n


def han(t):
    return sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")


def main():
    for s in (sys.stdout,):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    cid = card_id()
    od = out_dir(cid)
    od.mkdir(parents=True, exist_ok=True)
    meta_blob = {"built_at": "", "entries": 0, "mem_mtime": 0, "packs": {}}
    try:
        import sqlite3 as _sq
        import paths as _paths
        from datetime import datetime as _dt
        _p = _paths.paths()
        _con = _sq.connect(_p["db"])
        meta_blob["entries"] = _con.execute(
            "SELECT COUNT(*) FROM entries WHERE enabled=1 AND pending=0").fetchone()[0]
        _con.close()
        meta_blob["mem_mtime"] = Path(_p["db"]).stat().st_mtime
        meta_blob["built_at"] = _dt.now().strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        print(f"[warn] 取记忆版本失败：{e}", file=sys.stderr)

    jobs = []          # 2026-10-29：三包取消，改为「出差包 + 预设」

    ext = "--external" in sys.argv
    if ext:
        od = od / "外部客户端版"
        od.mkdir(parents=True, exist_ok=True)

    for name, scene, desk, head in jobs:
        body = build(scene, desk).rstrip()
        if ext:
            # 去掉模式标题与"启动包"字样，换成"角色设定"框架
            import re as _re
            body = _re.sub(r"^【[^】]*】\s*\n", "", body, count=1)
            body = body.replace("启动包", "资料")
            head = EXT_WORK if scene == "工作" else EXT_ROLE
            tail = EXT_TAIL
            body = body[body.find("（以下是"):] if "（以下是" in body else body
        else:
            tail = TAIL_WORK if scene == "工作" else TAIL_COMMON
        text = head + body + "\n\n" + tail + "\n"
        (od / name).write_text(text, encoding="utf-8")
        tag = "外部版" if ext else f"desk={desk}"
        print(f"[{cid}] {name}: {len(text)} 字符 / {han(text)} 汉字  （{tag}）")
        meta_blob["packs"][name] = {"scene": scene, "desk": desk, "han": han(text)}


    # 出差包（本体）+ 预设（卡清单），2026-10-29
    try:
        core_text, n_idx = build_core()
        (od / "出差包.txt").write_text(core_text + "\n", encoding="utf-8")
        print(f"[{cid}] 出差包.txt: {len(core_text)} 字符 / {han(core_text)} 汉字"
              f"（技能索引 {n_idx} 张）")
        meta_blob["packs"]["出差包.txt"] = {"scene": "日常", "desk": "none",
                                            "han": han(core_text)}
        meta_blob["core"] = {"skills_indexed": n_idx}
        for pname in PRESETS:
            text, ids = build_preset(pname, core_text)
            fn = f"预设-{pname}.txt"
            (od / fn).write_text(text + "\n", encoding="utf-8")
            print(f"[{cid}] {fn}: {len(text)} 字符 / {han(text)} 汉字"
                  f"（{'、'.join(ids) if ids else '无卡（预设里的卡不存在）'}）")
            meta_blob["packs"][fn] = {"cards": ids, "han": han(text)}
    except SystemExit as e:
        print(f"[warn] 出差包生成失败：{e}", file=sys.stderr)

    try:
        (od / "_meta.json").write_text(json.dumps(meta_blob, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[warn] 写 _meta.json 失败：{e}", file=sys.stderr)


if __name__ == "__main__":
    main()
