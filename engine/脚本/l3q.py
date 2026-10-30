#!/usr/bin/env python3
"""l3q：查询代理（她 → 我 → 片段）。

规则（定案见 `待办与待决.md` 第 13–18 条、`规格/查询代理-契约.md`）：
  · **不面向用户**：只回结构化片段给角色，不生成给用户的话；
  · 检索不花模型钱：有时间+关键词就走规则；只有模糊时才问规划器（默认规则，`HER_L3Q_PLANNER` 可接模型）；
  · 探针 ≤3：宽窗 → 加词 → 换角度；
  · 片段 ≤3 条、每条 ≤300 字、必带出处；
  · 查不到就说"没找到"，**不猜**；
  · 每次调用落一行审计（含"决定不查"）。

用法：
  python 脚本/l3q.py recall --question "还记得那次壳的事吗" --when 上周 --keywords 壳
  python 脚本/l3q.py recall --question "上次那个" --json
  python 脚本/l3q.py audit 20
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
L3 = DATA / "记忆" / "L3"
AUDIT = L3 / "_查询审计.jsonl"


# 观点/判断类：不是"发生过的事"，不该去查日志
OPINION = ("该不该", "要不要", "怎么看", "觉得", "你认为", "你觉得", "是不是应该",
           "好不好", "值不值", "对不对", "建议", "什么看法", "意见")

RECALL_SIGNALS = ["还记得", "记不记得", "之前", "以前", "上次", "那次", "那件事",
                  "当时", "那时候", "上回", "早期", "第几期", "第 1 期", "第1期"]
TIME_HINTS = ["今天", "昨天", "前天", "上周", "这周", "本周", "本月", "上个月", "上月",
              "月", "号", "日", "最近", "第"]
STOP = {"那个", "这个", "事情", "事", "东西", "什么", "怎么", "我们", "你", "我", "他", "她"}


def norm_words(question):
    """抽关键词：去掉回忆信号词、时间词、停用词，剩下的才算线索。"""
    q = question or ""
    for sig in RECALL_SIGNALS + TIME_HINTS:
        q = q.replace(sig, " ")
    q = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", " ", q)
    QW = ("什么", "怎么样", "怎么", "为什么", "了吗", "吗", "呢", "了", "吧",
          "我说", "你说", "说过", "那个", "这个", "事情", "那件", "件")
    words = []
    for w in q.split():
        for x in QW:
            w = w.replace(x, " ")
        for piece in w.split():
            if len(piece) < 2 or piece in STOP:
                continue
            if len(piece) > 4:
                # 中文没分词：长串拆成相邻二字（OR 命中即可）
                words.extend(piece[i:i + 2] for i in range(0, len(piece) - 1, 2))
            else:
                words.append(piece)
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:4]


def decide(question, when, keywords):
    """规则快筛：返回 (needed, plan, reason)。"""
    if any(w in (question or "") for w in OPINION):
        return False, [], "这是观点/判断类问题，不是「发生过的事」——不查日志，直接按自己想法答"
    has_signal = any(s in (question or "") for s in RECALL_SIGNALS)
    has_time = bool(when) or any(t in (question or "") for t in TIME_HINTS)
    kws = keywords or norm_words(question)
    if not kws and not when:
        return False, [], ("线索太少（没有时间、也没有具体的关键词）"
                           "——先问用户一个具体线索，不要猜")
    if not has_signal and not has_time and not kws:
        return False, [], "不像回忆类问题（无回忆信号、无时间、无关键词）"
    if when and kws:
        return True, [{"when": when, "kw": kws}], "时间+关键词齐全，规则直查"
    if kws and not when:
        return True, [{"when": None, "kw": kws}], "有关键词无线索时间，先宽查"
    if when and not kws:
        return True, [{"when": when, "kw": []}], "只有时间，按时间窗扫"
    return False, [], "线索太少（无时间、无关键词），建议先问用户"


def run_grep(when, keywords, limit=8):
    """调 l3.py grep 拿命中行（复用现成实现，避免两套检索）。"""
    args = [sys.executable, str(ROOT / "脚本" / "l3.py"), "grep"]
    text = " ".join(keywords) if keywords else " "
    args.append(text or " ")
    if when:
        args += ["--when", when]
    if len(keywords) > 1:
        args += ["--any"]
    args += ["--limit", str(limit)]
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    out = (p.stdout or "").strip()
    hits = []
    for line in out.splitlines():
        m = re.match(r"^\[(\S+)\]\s+(\S+):(\d+)\s+(.*)$", line)
        if m:
            hits.append({"src": m.group(1), "file": m.group(2), "line": int(m.group(3)),
                         "text": m.group(4).strip()})
    return hits, out


def to_fragments(hits, limit=3, excerpt=300):
    out, seen = [], set()
    for h in hits:
        key = h["file"]
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "id": h["file"].replace(".md", "").replace(".json", ""),
            "file": h["file"],
            "date": "",
            "topic": "",
            "excerpt": h["text"][:excerpt],
            "source": "L3 日志原文",
            "lines": str(h["line"]),
        })
        if len(out) >= limit:
            break
    return out


def recall(question, when, keywords, budget=3, session=""):
    needed, plan, reason = decide(question, when, keywords)
    probes, hits = [], []
    status = "need_more"
    note = reason

    if needed:
        attempts = list(plan)
        kw = keywords or norm_words(question)
        if when:
            attempts.append({"when": when.rsplit("月", 1)[0] + "月" if "月" in when else when, "kw": kw})
        if kw:
            attempts.append({"when": when, "kw": kw[:1]})
        attempts = attempts[:budget]
        for i, at in enumerate(attempts, 1):
            h, _ = run_grep(at.get("when"), at.get("kw") or [])
            probes.append({"n": i, "when": at.get("when") or "", "keywords": at.get("kw") or [], "hits": len(h)})
            if h:
                hits = h
                status = "found"
                note = f"第 {i} 次探针命中 {len(h)} 行"
                break
        if status != "found":
            status = "none"
            note = "探了 %d 次，日志里没找到这一段" % len(probes)

    frags = to_fragments(hits) if status == "found" else []
    result = {"status": status, "fragments": frags, "probes": probes, "note": note,
              "question_hash": hashlib.sha256((question or "").encode()).hexdigest()[:8]}
    audit(result, question, when, keywords, session)
    return result


def audit(result, question, when, keywords, session):
    rec = {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "q_hash": result["question_hash"],
        "q_head": (question or "")[:20],
        "when": when or "",
        "kind": "l3q",
        "status": result["status"],
        "hits": len(result["fragments"]),
        "probes": len(result["probes"]),
        "probe_detail": [{"n": p.get("n"), "when": p.get("when"), "kw": p.get("keywords"),
                          "hits": p.get("hits")} for p in result["probes"]],
        "plans": 0,
        "session": session,
        "files": [f["file"] for f in result["fragments"]],
    }
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def render_text(result):
    if result["status"] == "need_more":
        if "观点" in result.get("note", ""):
            return f"[不用查] {result['note']}"
        return f"[需要更多线索] {result['note']}\n问题：_问一个具体线索（大约是几月？跟稿子还是素材有关？）_"
    if result["status"] == "none":
        return f"[没找到] {result['note']}\n（照实说『日志里没找到这段』，不要编）"
    lines = [f"[找到 {len(result['fragments'])} 条片段｜日志原文，不是记忆]"]
    for f in result["fragments"]:
        lines.append(f"- {f['id']}（{f['source']}，第 {f['lines']} 行）：{f['excerpt']}")
    lines.append("（引用要标出处；不要当自己的记忆复述；只用在这一轮）")
    return "\n".join(lines)


def cmd_recall(a):
    r = recall(a.question, a.when, [k for k in re.split(r"[\s,，、]+", a.keywords or "") if k],
               a.budget, a.session)
    print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else render_text(r))


def cmd_audit(a):
    if not AUDIT.exists():
        print("还没有查询审计")
        return
    lines = AUDIT.read_text(encoding="utf-8").splitlines()[-a.n:]
    for line in lines:
        try:
            d = json.loads(line)
        except Exception:
            continue
        print(f"{d['ts']}  {d['status']:<9} probes={d.get('probes',0)} hits={d.get('hits',0)}  {d.get('q_head','')}")


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="查询代理（只回片段给角色）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("recall")
    p.add_argument("--question", required=True)
    p.add_argument("--when", default="")
    p.add_argument("--keywords", default="")
    p.add_argument("--budget", type=int, default=3)
    p.add_argument("--session", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_recall)

    p = sub.add_parser("audit"); p.add_argument("n", type=int, nargs="?", default=10)
    p.set_defaults(fn=cmd_audit)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
