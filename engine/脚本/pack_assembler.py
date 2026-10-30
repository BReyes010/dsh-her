#!/usr/bin/env python3
"""pack_assembler —— 构建期装配器（Step 2，卡格式 v1）。

职责：任务 → 卡集合 → 装前校验 → 拼包 → 写 manifest（三态 + injected_hash）→ 包尾盖章。
不做：运行时按需装（那是 C）；不改人格卡/记忆库；不覆盖出差包与预设（默认写 启动包/装配版/）。

用法：
  python 脚本/pack_assembler.py list                    # 列卡与任务
  python 脚本/pack_assembler.py check --task 写稿        # 只校验，不写文件
  python 脚本/pack_assembler.py build --task 写稿 --material 素材.md
  python 脚本/pack_assembler.py build --task 直播
退出码：0 成功 / 2 校验失败（拒装）/ 3 用法错误
"""

import argparse
import hashlib
import json
import os as _os
import re
import sys
from datetime import datetime
from pathlib import Path

from paths import rel

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
TASKS_FILE = SKILLS / "_tasks.json"
POLICY_FILE = SKILLS / "_policy.json"
OUTDIR = ROOT / "启动包" / "装配版"

DATA = Path(_os.environ["HER_HOME"]).resolve() if _os.environ.get("HER_HOME") else ROOT
PERSONA = DATA / "人格" / "人格卡.md"
MEM_MD = DATA / "记忆" / "记忆.md"
STATE_SRC = DATA / "启动包" / "日常模式.txt"

REQ = re.compile(r"^(material|card):([\u4e00-\u9fffA-Za-z0-9_\-]+)(>=(\d+))?$")
SAFE_ID = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9_\-]{1,40}$")
INJECTION = re.compile(r"(忽略(之前|以上|前面)的?(规则|指令|设定)|你现在是|system\s*prompt|修改你的设定)", re.I)


class Err(Exception):
    pass


def han(t):
    return sum(1 for c in t if "\u4e00" <= c <= "\u9fff")


def parse_card(p):
    raw = p.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
    if not m:
        raise Err(f"E01 {p.name}: frontmatter 缺失")
    fm, body = m.group(1), m.group(2)
    d, prov = {}, {}
    for line in fm.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  "):                      # provenance 子字段
            k, _, v = line.strip().partition(":")
            prov[k.strip()] = v.strip().strip("'\"")
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            d[k] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        elif re.fullmatch(r"-?\d+", v):
            d[k] = int(v)
        else:
            d[k] = v
    if prov:
        d["provenance"] = prov
    d["_path"] = p.name
    d["_body"] = body
    d["_han"] = han(body)
    d["_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return d


def load_cards():
    if not SKILLS.exists():
        return {}
    out = {}
    for p in sorted(SKILLS.glob("*.md")):
        if p.name.startswith("_"):
            continue
        c = parse_card(p)
        cid = c.get("id") or p.stem
        if cid in out:
            raise Err(f"E02 重复 id：{cid}")
        out[cid] = c
    return out


def load_policy():
    if not POLICY_FILE.exists():
        return {"max_cards": 1, "material_max_han": 1500}
    try:
        return json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"max_cards": 1, "material_max_han": 1500}


def load_tasks():
    if not TASKS_FILE.exists():
        return {}
    return json.loads(TASKS_FILE.read_text(encoding="utf-8")).get("tasks", {})


def check_fields(c):
    cid = c.get("id", "?")
    for k in ("kind", "id", "version", "when", "slot", "budget_han"):
        if k not in c:
            raise Err(f"E02 {cid}: 缺必填字段 {k}")
    if c["kind"] not in ("skill", "memory", "task"):
        raise Err(f"E03 {cid}: kind 只能是 skill / memory / task")
    if c["kind"] == "task":
        if not c.get("skill"):
            raise Err(f"E20 {cid}: 任务卡必须写 skill（绑哪张技能卡）")
        if c.get("slot") not in (None, "task"):
            raise Err(f"E20 {cid}: 任务卡的 slot 只能是 task")
    if c.get("slot") not in (None, "none", "tone", "task"):
        raise Err(f"E04 {cid}: slot 只能是 none / tone / task")
    if not SAFE_ID.match(str(c["id"])):
        raise Err(f"E02 {cid}: id 含非法字符")
    if c["kind"] == "memory" and "provenance" not in c:
        raise Err(f"E11 {cid}: 记忆卡必须有 provenance(source,date)")
    if c["kind"] == "skill" and "provenance" in c:
        raise Err(f"E11 {cid}: 技能卡不该带 provenance")
    for r in c.get("requires", []) + c.get("provides", []):
        if not REQ.match(r):
            raise Err(f"E04 {cid}: 「{r}」不是受控枚举（card: / material:，可带 >=N）")
    if c.get("on_over_budget") not in (None, "refuse", "warn"):
        raise Err(f"E04 {cid}: on_over_budget 只能是 refuse / warn")
    body = c["_body"]
    if INJECTION.search(body):
        raise Err(f"E14 {cid}: 卡内含疑似注入指令，拒装")
    if c["_han"] > int(c["budget_han"]):
        if c.get("on_over_budget", "refuse") == "refuse":
            raise Err(f"E12 {cid}: 超出预算 {c['_han']}/{c['budget_han']}")
    if len(c["_body"]) > 6000 * 3:
        raise Err(f"E12 {cid}: 单卡过长")
    # E15：tone 卡必须有样本区
    if c.get("slot") == "tone" and "part: tone-samples" not in body:
        raise Err(f"E15 {cid}: tone 卡必须有 <!-- part: tone-samples --> 样本区")


def validate(ids, catalog, task=None, tasks=None):
    """返回 (loaded_cards, warnings)。任何硬错误抛 Err。"""
    for cid in ids:
        if cid not in catalog:
            raise Err(f"E05 卡不存在：{cid}")
    for cid in ids:
        check_fields(catalog[cid])

    tone = [cid for cid in ids if catalog[cid].get("slot") == "tone"]
    if len(tone) > 1:
        raise Err(f"E08 tone 槽冲突：{tone}")

    have = set(ids)
    provides = {r for cid in ids for r in catalog[cid].get("provides", [])}
    for cid in ids:
        c = catalog[cid]
        for r in c.get("requires", []):
            kind, name, _, ver = REQ.match(r).groups()
            if kind == "card":
                if name not in have:
                    raise Err(f"E05 {cid}: 依赖卡缺失 card:{name}")
                if ver and int(catalog[name]["version"]) < int(ver):
                    raise Err(f"E07 {cid}: 依赖版本不满足 card:{name}>={ver}（实际 {catalog[name]['version']}）")
            else:
                if r not in provides:
                    raise Err(f"E05 {cid}: 依赖素材缺失 {r}（没有卡声明 provides）")
        for other in c.get("conflicts", []):
            if other in have:
                raise Err(f"E09 {cid}: 与 {other} 互斥")

    topics = {}
    for cid in ids:
        tp = catalog[cid].get("topic")
        if tp:
            topics.setdefault(tp, []).append(cid)
    for tp, cids in topics.items():
        if len(cids) > 1:
            raise Err(f"E10 同 topic「{tp}」多卡：{cids}（须显式选一）")

    warns = []
    if task and tasks and task in tasks:
        for r in tasks[task].get("requires", []):
            if r not in provides:
                raise Err(f"E06 任务依赖缺失 {r}（这次任务要用的素材没装）")

    # 装载策略（2026-10-29）：技能卡 ≤max_cards；素材合计 ≤material_max_han；任务卡不占额度
    pol = load_policy()
    kinds = {cid: catalog[cid].get("kind") for cid in ids}
    skills = [c for c in ids if kinds[c] == "skill"]
    mats = [c for c in ids if kinds[c] == "memory"]
    tasks = [c for c in ids if kinds[c] == "task"]
    if len(skills) > int(pol.get("max_cards", 1)):
        raise Err(f"E18 技能卡超限：{len(skills)} 张 > max_cards={pol.get('max_cards')}"
                  "（素材与任务卡不占额度）")
    mh = sum(catalog[c]["_han"] for c in mats)
    if mh > int(pol.get("material_max_han", 1500)):
        raise Err(f"E19 素材超限：{mh} 汉字 > material_max_han={pol.get('material_max_han')}")
    for t in tasks:
        parent = catalog[t].get("skill")
        if not parent:
            raise Err(f"E20 {t}：任务卡必须写 skill（绑哪张技能卡）")
        if parent not in ids:
            raise Err(f"E20 {t}：父卡「{parent}」不在这次装载里")

    total = sum(catalog[cid]["_han"] for cid in ids)
    if task and tasks and task in tasks:
        cap = tasks[task].get("budget_han")
        if cap and total > int(cap):
            raise Err(f"E12 任务超预算：{total} > {cap}")
    if total > 8000:
        warns.append(f"总预算偏高：{total} 汉字 > 8000")
    # 时效
    stale = []
    for cid in ids:
        c = catalog[cid]
        sa = c.get("stale_after")
        if sa and isinstance(c.get("provenance"), dict):
            try:
                n = int(str(sa).rstrip("d"))
                d = datetime.strptime(c["provenance"]["date"], "%Y-%m-%d")
                if (datetime.now() - d).days > n:
                    stale.append(cid)
                    warns.append(f"{cid}: 已过期（{c['provenance']['date']}，stale_after={sa}）")
            except Exception:
                warns.append(f"{cid}: stale_after/date 解析失败，忽略")
    return stale, warns


def core_text():
    persona = PERSONA.read_text(encoding="utf-8-sig").strip()
    mem = MEM_MD.read_text(encoding="utf-8-sig")
    m = re.search(r"^##\s*条目索引\s*$(.*?)(?=^##\s|\Z)", mem, re.S | re.M)
    const = ""
    if m:
        # 常驻层 = 条目索引里"常驻层"那几条的正文；这里只取索引段作为摘要，避免整库进包
        const = "## 我记得的事（索引）\n\n" + m.group(1).strip()[:1200]
    state = ""
    if STATE_SRC.exists():
        t = STATE_SRC.read_text(encoding="utf-8")
        mm = re.search(r"^##\s*现在几号\s*$(.*?)(?=^##\s|\Z)", t, re.S | re.M)
        if mm:
            state = ("## 现在几号" + mm.group(1)).strip()
    return "\n\n".join(x for x in (persona, const, state) if x.strip())


def assemble(task, catalog, tasks, extra=()):
    spec = tasks.get(task)
    if not spec:
        raise Err(f"E00 未知任务：{task}（可用：{sorted(tasks)}）")
    ids = list(dict.fromkeys(list(spec.get("cards", [])) + list(extra)))
    stale, warns = validate(ids, catalog, task=task, tasks=tasks)
    core = core_text()
    blocks = []
    for cid in ids:
        c = catalog[cid]
        blocks.append(f"<!-- card {cid}@{c['version']} {c['_hash']} -->\n{c['_body'].strip()}")
    text = core + "\n\n" + "\n\n".join(blocks)
    seal = ("装配状态: core@v15 ｜ 已装: " + ("、".join(ids) or "无")
            + " ｜ tone: " + ("、".join(i for i in ids if catalog[i].get("slot") == "tone") or "无")
            + " ｜ 未装能力: " + ("、".join(sorted(
                c for c in catalog
                if catalog[c]["kind"] == "skill" and c not in ids)) or "无")
            + " ｜ 素材: " + ("有" if any(catalog[i]["kind"] == "memory" for i in ids) else "**空**")
            + " ｜ 旧卡: " + ("、".join(stale) or "无")
            + f" ｜ 策略: 技能卡≤{load_policy().get('max_cards', 1)}")
    text = text + "\n\n" + "==== 背景到此为止 ====\n" + seal + "\n"

    # E16：tone 卡的样本区必须逐字进包（不许被摘要/压缩）
    for cid in ids:
        c = catalog[cid]
        if c.get("slot") != "tone":
            continue
        m = re.search(r"<!--\s*part:\s*tone-samples\s*-->(.*?)(?=<!--\s*part:|\Z)", c["_body"], re.S)
        sample = (m.group(1) if m else "").strip()
        if not sample:
            raise Err(f"E15 {cid}: tone 卡样本区为空")
        probe = sample.splitlines()[0].strip()
        if probe and probe not in text:
            raise Err(f"E16 {cid}: 样本区没有逐字进包（可能被压缩）")

    injected = [f"core@v15"] + [f"{i}@{catalog[i]['version']}" for i in ids]
    manifest = {
        "task": task,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "requested": ids,
        "loaded": ids,
        "injected": injected,
        "injected_hash": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "han": han(text),
        "cards": {i: {"version": catalog[i]["version"], "hash": catalog[i]["_hash"],
                      "han": catalog[i]["_han"], "kind": catalog[i]["kind"]} for i in ids},
        "warnings": warns,
    }
    return text, manifest


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="构建期装配器（卡格式 v1）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("check", "build"):
        p = sub.add_parser(name)
        p.add_argument("--task", required=True)
        p.add_argument("--material", action="append", default=[], help="额外装入的卡 id（可多次）")
        p.add_argument("--out")
    a = ap.parse_args()

    try:
        catalog = load_cards()
        tasks = load_tasks()
    except Err as e:
        print(f"[拒绝] {e}")
        sys.exit(2)

    if a.cmd == "list":
        print(f"卡（{len(catalog)}）：")
        for cid, c in sorted(catalog.items()):
            print(f"  {cid:<10} kind={c['kind']:<6} slot={c.get('slot','none'):<5} "
                  f"{c['_han']:>4} 汉字 v{c['version']}  when={c.get('when', [])}")
        print(f"\n任务（{len(tasks)}）：")
        for t, spec in tasks.items():
            print(f"  {t:<8} cards={spec.get('cards')} requires={spec.get('requires', [])}")
        return

    try:
        text, man = assemble(a.task, catalog, tasks, a.material)
    except Err as e:
        print(f"[拒绝] {e}")
        sys.exit(2)

    print(f"任务：{a.task}")
    print(f"已装：{man['requested']}")
    print(f"总汉字：{man['han']}")
    print(f"injected_hash：{man['injected_hash'][:24]}…")
    for w in man["warnings"]:
        print(f"  ! {w}")

    if a.cmd == "check":
        print("校验通过（未写文件）")
        return

    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = Path(a.out) if a.out else OUTDIR / f"{a.task}.txt"
    out.write_text(text, encoding="utf-8")
    (OUTDIR / f"{a.task}.manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写：{rel(out)}")
    print(f"      {rel(OUTDIR)}/{a.task}.manifest.json")

    # 自检：重算 hash
    again = "sha256:" + hashlib.sha256(out.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    print(f"对账：{'OK' if again == man['injected_hash'] else 'MISMATCH'}")
    if again != man["injected_hash"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
