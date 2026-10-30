#!/usr/bin/env python3
"""workbench：任务看板的真源与渲染（按{角色名}的六条验收）。

真源：入口/任务看板.json      视图：入口/任务看板.md（自动生成，手改会被拦）
结构：现在 / 已定 / 卡在哪 / 下一步（+ 踩过的坑、附录：已作废）

六条验收对应：
  1 命令齐：set-now / decide / retire / pitfall / blocker / next / render（另有 import-md / compare / flip / status）
  2 render 前比 hash：md 被手改 → 先存 任务看板.manual.md，再覆盖，不静默
  3 retired 折叠：正文只显示 active，retired 进附录
  4 没有 delete：接口里没有任何删除路径，作废只能 retire
  5 迁移双写：mode=dual 时 md 被手改不覆盖，先提示 sync-from-md；不锁死
  6 json 少了条目 → render 拒绝并报警（基线 decisions_count）

用法：
  python workbench.py status
  python workbench.py import-md [--md 路径]        # 迁移：md → json
  python workbench.py compare                      # json 渲染 vs 现 md 的差异
  python workbench.py set-now "…"
  python workbench.py decide "结论" --pointer 路径
  python workbench.py retire D-02 --why "…"
  python workbench.py pitfall "踩过的坑"
  python workbench.py blocker add "…" | blocker clear
  python workbench.py next set "1" "2" | next clear
  python workbench.py render [--force]
  python workbench.py flip                         # dual → json 单源

退出码：0 正常；2 参数/状态错；3 拒绝执行（手改/条目减少）
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
MD = DATA / "入口" / "任务看板.md"
JS = DATA / "入口" / "任务看板.json"
MANUAL = DATA / "入口" / "任务看板.manual.md"
STAMP_RE = re.compile(r"^最后更新：.*$", re.M)
ARC = DATA / "记忆" / "L3" / "归档" / "任务看板-已作废.md"
ARC_META = DATA / "记忆" / "L3" / "归档" / "_归档.json"
ARC_GEN_RE = re.compile(r"^> 生成：.*$", re.M)


def archive_state():
    """返回 (ok, ids, 原因)。sha 校验不过一律当不可用——宁可多渲染附录，也不静默错位。"""
    if not (ARC.exists() and ARC_META.exists()):
        return False, set(), "没有归档文件"
    try:
        meta = json.loads(ARC_META.read_text(encoding="utf-8-sig"))
    except Exception as e:
        return False, set(), f"归档元数据读不了：{e}"
    # 按**文件实际字节**比：Windows 下 write_text 会把 \n 写?? \r\n，
    # 拿字符串重新算 sha 永远对不上（2026-10-28 修）
    if hashlib.sha256(ARC.read_bytes()).hexdigest() != meta.get("sha256"):
        return False, set(), "归档文件 sha256 与 _归档.json 不符（被手改过？先重跑 wb_archive.py）"
    return True, set(meta.get("ids", [])), "ok"


def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


BOARD_LOG = DATA / "记忆" / "L3" / "_任务看板.jsonl"


def board_log(kind, detail, before_sha="", after_sha=""):
    """每次改动记一行：谁改的、改了哪类、前后 sha。审计不依赖票据。"""
    import json as _j
    from datetime import datetime as _dt
    try:
        BOARD_LOG.parent.mkdir(parents=True, exist_ok=True)
        with BOARD_LOG.open("a", encoding="utf-8") as f:
            f.write(_j.dumps({"ts": _dt.now().strftime("%Y-%m-%d %H:%M"),
                              "kind": kind, "detail": detail[:200],
                              "before": before_sha[:16], "after": after_sha[:16]},
                             ensure_ascii=False) + "\n")
    except Exception:
        pass


def load():
    if not JS.exists():
        sys.exit("没有 任务看板.json：先跑 import-md")
    return json.loads(JS.read_text(encoding="utf-8-sig"))


def save(d):
    # 每次写都记一次时间：以前只在建库时写，"最后更新"永远停在 2026-10-27（2026-10-29 修）
    d["updated"] = now()
    JS.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def next_id(d):
    """只在"全部条目"上取最大号 +1：作废的号也不许复用（{角色名}的 D16 同源教训）。"""
    mx = 0
    for x in d.get("decisions", []):
        m = re.match(r"^D-(\d+)$", str(x.get("id", "")))
        if m:
            mx = max(mx, int(m.group(1)))
    return f"D-{mx + 1:02d}"


# ---------------- 解析 / 渲染 ----------------

def parse_md(text):
    lines = text.splitlines()
    header, sections, cur = [], {}, None
    for ln in lines:
        if ln.startswith("## "):
            cur = ln[3:].strip()
            sections[cur] = []
            continue
        if cur is None:
            header.append(ln)
        else:
            sections[cur].append(ln)

    def bullets(name):
        out = []
        for ln in sections.get(name, []):
            s = ln.strip()
            if s.startswith("- "):
                out.append(s[2:].strip())
        return out

    def numbered(name):
        out = []
        for ln in sections.get(name, []):
            m = re.match(r"^\d+\.\s+(.*)$", ln.strip())
            if m:
                out.append(m.group(1).strip())
        return out

    def para(name):
        body = [ln for ln in sections.get(name, []) if ln.strip()]
        return body[0].strip() if body else ""

    hdr = "\n".join(header)
    m = STAMP_RE.search(hdr)
    if m:
        d0 = re.search(r"最后更新：([0-9\-]{10} [0-9:]{5})", m.group(0))
        if d0:
            import __main__
        hdr = STAMP_RE.sub("", hdr)
    hdr = hdr.strip("\n")
    # 已定 的名字里可能带括号说明，统一识别
    dec_key = next((k for k in sections if k.startswith("已定")), None)
    pit_key = next((k for k in sections if k.startswith("踩过的坑")), None)
    app_key = next((k for k in sections if k.startswith("附录")), None)
    # 「已定」两种写法都认：
    #   旧：- <结论全文>
    #   新：- D-03｜<摘要>｜→ `路径`      ← 指针表
    decisions, seq = [], 0
    for raw in (bullets(dec_key) if dec_key else []):
        m = re.match(r"^(D-\d+)｜(.+?)(?:｜→\s*`(.+)`)?$", raw)
        if m:
            decisions.append({"id": m.group(1), "summary": m.group(2).strip(),
                              "pointer": (m.group(3) or "").strip(), "text": "",
                              "date": now()[:10], "status": "active"})
        else:
            seq += 1
            decisions.append({"id": f"D-{seq:02d}", "text": raw, "summary": "",
                              "pointer": "", "date": now()[:10], "status": "active"})

    # 附录：已作废 —— 必须读回来（保 id / 日期 / 理由），否则切 json 单源后永久消失
    retired = []
    for ln in (sections.get(app_key, []) if app_key else []):
        s = ln.strip()
        m = re.match(r"^-\s+~~(.+?)~~（(.*?)）$", s)
        if not m:
            continue
        text, meta = m.group(1).strip(), m.group(2).strip()
        # 位置无关：id / 日期 各自识别，剩下的都算理由（兼容"无 id 老行"、"理由里带｜"）
        mid = re.search(r"\bD-\d+\b", meta)
        rid = mid.group(0) if mid else ""
        mdate = re.search(r"\d{4}-\d{2}-\d{2}", meta)
        rdate = mdate.group(0) if mdate else ""
        rest = meta
        for tok in (rid, rdate):
            if tok:
                rest = rest.replace(tok, "", 1)
        why = "｜".join(p.strip() for p in rest.split("｜") if p.strip())
        why = re.sub(r"^[，,、\s]+", "", why)
        retired.append({"id": rid, "text": text, "pointer": "", "date": rdate or now()[:10],
                        "status": "retired", "why": why, "retired_at": rdate or now()[:10]})
    # 补号：老格式没有 id 的，按"全表最大号 +1"续（不复用）
    used = {x["id"] for x in decisions if x.get("id")}
    mx = max([int(re.match(r"^D-(\d+)$", i).group(1)) for i in used if re.match(r"^D-(\d+)$", i)] or [0])
    for r in retired:
        if not r["id"]:
            mx += 1
            r["id"] = f"D-{mx:02d}"
    decisions += retired
    return {
        "version": 1, "mode": "dual", "updated": now(), "client": "DSH",
        "header": hdr,
        "now": para("现在"),
        "decisions": decisions,
        "blockers": bullets("卡在哪"),
        "next": numbered("下一步"),
        "pitfalls": [{"text": t, "date": now()[:10]} for t in (bullets(pit_key) if pit_key else [])],
        "baseline": {"decisions": len(decisions)},
        "md_sha": "",
    }


def render_text(d):
    active = [x for x in d["decisions"] if x.get("status") == "active"]
    retired = [x for x in d["decisions"] if x.get("status") != "active"]
    arc_ok, _, _ = archive_state()
    out = [(d.get("header") or "# 任务看板（跨客户端交接单）").rstrip("\\n"), "",
           f"最后更新：{d.get('updated')} · 客户端：{d.get('client')}", "",
           "## 现在", "", d.get("now") or "（空）", "",
           "## 已定（细节见所指文件，勿重开）", ""]
    if active:
        for x in active:
            one = x.get("summary") or x.get("text", "")
            tail = f"｜→ `{x['pointer']}`" if x.get("pointer") else ""
            out.append(f"- {x['id']}｜{one}{tail}")
    else:
        out.append("（空）")
    out += ["", "## 卡在哪", ""]
    out += [f"- {b}" for b in d.get("blockers", [])] or ["（空）"]
    out += ["", "## 下一步", ""]
    out += [f"{i}. {s}" for i, s in enumerate(d.get("next", []), 1)] or ["（空）"]
    if d.get("pitfalls"):
        out += ["", "## 踩过的坑", ""]
        out += [f"- {p['text']}" for p in d["pitfalls"]]   # 日期只存 json，不重复渲染
    if arc_ok:
        out += ["", "> 已作废条目见 `记忆\\L3\\归档\\任务看板-已作废.md`"
                "（按编号查：`python 脚本\\wb_query.py D-xx`）"]
    elif retired:
        out += ["", "## 附录：已作废（留痕，不删）", ""]
        for x in retired:
            out.append(f"- ~~{x['text']}~~（{x.get('id','')}｜{x.get('retired_at') or x.get('date','')}"
                       f"｜{x.get('why','')}）")
    return "\n".join(out).rstrip() + "\n"




# ── 2026-10-28：任务看板清理机制（首期 15 条 / 900 汉字，两周后收到 12/800）──
LIMIT_DECISIONS = 15
LIMIT_HAN = 900
RENDER_LOG = ROOT / "入口" / "_render_log.jsonl"
FACT_WORDS = ("已发布", "已交付", "已修", "已完成", "已就绪", "已落地", "已合并", "已归档")
HEDGE_WORDS = ("待", "等", "未", "没")
BEHAVIOR_WORDS = ("不许", "不要", "禁止", "一律", "必须", "只许")
KEEP_DAYS = 30


def _today():
    from datetime import date
    return date.today()


def prune_candidates(d):
    """[(类别, 条目, 建议命令)]；行为约束类与 keep 未到期的不进候选。"""
    from datetime import date
    out = []
    today = _today()
    for x in d.get("decisions", []):
        if x.get("status") != "active":
            continue
        text = x.get("text") or ""
        why = x.get("keep_why") or ""
        if why:
            kept = x.get("kept_at") or ""
            try:
                if kept and (today - date.fromisoformat(kept)).days < KEEP_DAYS:
                    continue
            except Exception:
                pass
        if any(w in text for w in BEHAVIOR_WORDS):
            continue

        cat = None
        exp = x.get("expires") or ""
        if exp:
            try:
                if date.fromisoformat(exp) <= today:
                    cat = "显式过期"
            except Exception:
                pass
        if not cat and any(w in text for w in FACT_WORDS) and not any(w in text for w in HEDGE_WORDS):
            cat = "事实型"
        if not cat:
            dt = (x.get("date") or "")[:10]
            try:
                if dt and (today - date.fromisoformat(dt)).days >= 7:
                    cat = "超龄"
            except Exception:
                pass
        if not cat and not (x.get("pointer") or "").strip():
            cat = "无指针"
        if cat:
            cmd = f'python 脚本/workbench.py retire {x["id"]} --why "…" --by D-xx'
            if cat == "无指针":
                cmd = f'给 {x["id"]} 补 pointer，或 retire'
            out.append((cat, x, cmd))
    return out


def render_gate(d):
    active = [x for x in d.get("decisions", []) if x.get("status") == "active"]
    md = render_text(d)
    han = sum(1 for c in md if "\u4e00" <= c <= "\u9fff")
    reasons = []
    if len(active) > LIMIT_DECISIONS:
        extra = ", ".join(x["id"] for x in active[LIMIT_DECISIONS:])
        reasons.append(f"已定 {len(active)} 条 > 上限 {LIMIT_DECISIONS}（超限：{extra}）")
    if han > LIMIT_HAN:
        reasons.append(f"渲染后 {han} 汉字 > 上限 {LIMIT_HAN}")
    return (bool(reasons), reasons, len(active), han)


def log_render(why, active, han):
    from datetime import datetime as _dt
    rec = {"ts": _dt.now().strftime("%Y-%m-%d %H:%M"), "force": True, "why": why,
           "active": active, "han": han}
    RENDER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RENDER_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def cmd_prune(_):
    d = load()
    cands = prune_candidates(d)
    active = [x for x in d.get("decisions", []) if x.get("status") == "active"]
    md = render_text(d)
    han = sum(1 for c in md if "\u4e00" <= c <= "\u9fff")
    if not cands:
        print(f"没有候选（已定 {len(active)}/{LIMIT_DECISIONS} 条，{han}/{LIMIT_HAN} 汉字）")
        sys.exit(0)
    for cat, x, cmd in cands:
        summary = (x.get("summary") or x.get("text") or "")[:40]
        print(f"[{cat}] {x['id']}｜{summary}")
        print(f"        → {cmd}")
    print(f"\n已达：{len(active)}/{LIMIT_DECISIONS} 条，{han}/{LIMIT_HAN} 汉字｜候选 {len(cands)} 条")
    print("只出建议，不自动退——退休一条就少一条「当时为什么这么定」。")
    sys.exit(1)


def cmd_keep(a):
    d = load()
    for x in d.get("decisions", []):
        if x.get("id") == a.id:
            x["kept_at"] = _today().isoformat()
            x["keep_why"] = a.why
            x["keep_count"] = int(x.get("keep_count") or 0) + 1
            JS.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            n = x["keep_count"]
            print(f"[OK] {a.id} 已 keep（第 {n} 次，{KEEP_DAYS} 天内不再进候选）")
            if n >= 3:
                print(f"[!] 已 keep {n} 次：要么它真重要（写进文档/加指针），要么该退了")
            return
    sys.exit(f"没有 {a.id}")


def cmd_render(a):
    d = load()
    problems = []   # 子步骤失败集中汇总：有一项失败，收尾就非 0（不许静默绿）
    # 收尾第一步：把 retired 条目同步进 L3 归档（否则 wb_query 查不到新作废的）
    arc_script = ROOT / "脚本" / "wb_archive.py"
    if arc_script.exists() and not getattr(a, "no_archive", False):
        import subprocess
        r = subprocess.run([sys.executable, str(arc_script)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            first = (r.stdout or "").strip().splitlines()
            print(f"     [归档] {first[0] if first else 'ok'}")
        else:
            print(f"[!] 归档同步失败：{(r.stderr or '').strip()[:160]}", file=sys.stderr)
            problems.append("归档同步")

    arc_ok, _, why = archive_state()
    if ARC.exists() and not arc_ok and not getattr(a, "force", False):
        print(f"[X] 拒绝渲染：{why}", file=sys.stderr)
        print("    先跑 python 脚本/wb_archive.py 重新生成归档；或加 --force 忽略", file=sys.stderr)
        sys.exit(3)
    over, reasons, active_n, han_n = render_gate(d)
    if over:
        if not (getattr(a, "force", False) and getattr(a, "why", "")):
            print("[X] 拒绝收尾：任务看板超限", file=sys.stderr)
            for r in reasons:
                print(f"    - {r}", file=sys.stderr)
            print("    看该退哪些：python 脚本/workbench.py prune", file=sys.stderr)
            print('    放行：render --force --why "理由"（写 _render_log.jsonl）', file=sys.stderr)
            sys.exit(1)
        log_render(a.why, active_n, han_n)
        print(f"[!] 超限放行（已记 _render_log.jsonl）：{a.why}", file=sys.stderr)

    new = render_text(d)

    # 验收 6：json 条目少了 → 拒绝
    base = d.get("baseline", {}).get("decisions", 0)
    if len(d["decisions"]) < base:
        print(f"[X] 拒绝渲染：decisions 从 {base} 掉到 {len(d['decisions'])}（json 被删条了）", file=sys.stderr)
        sys.exit(3)

    # 验收 2：md 被手改 → 先存 manual 副本
    if MD.exists():
        cur = MD.read_text(encoding="utf-8")
        stored = d.get("md_sha") or ""
        hand_edited = stored and sha(STAMP_RE.sub("", cur)) != stored
        if hand_edited:
            if d.get("mode") == "dual" and not a.force:
                MANUAL.write_text(cur, encoding="utf-8")
                print("[!] md 被手改过（双写期）：已存 任务看板.manual.md", file=sys.stderr)
                print("  处理：python 脚本/workbench.py import-md   把 md 的改动并回 json；", file=sys.stderr)
                print("        确认无误后再 render；要强推用 --force", file=sys.stderr)
                sys.exit(3)
            MANUAL.write_text(cur, encoding="utf-8")
            print("[!] md 被手改过：已存 任务看板.manual.md，再覆盖", file=sys.stderr)

    # 「最后更新」是**内容**时间（谁改了台账谁写），渲染不改它
    if not d.get("updated"):
        d["updated"] = now()
    _before = sha(MD.read_text(encoding="utf-8")) if MD.exists() else ""
    new = render_text(d)
    MD.write_text(new, encoding="utf-8")
    d["md_sha"] = sha(STAMP_RE.sub("", new))
    d["baseline"] = {"decisions": len(d["decisions"])}
    save(d)
    board_log("render", f"decisions {len(d['decisions'])} 条", _before,
              sha(MD.read_text(encoding="utf-8")))
    act = sum(1 for x in d["decisions"] if x.get("status") == "active")
    ret = len(d["decisions"]) - act
    print(f"[OK] 已渲染 {MD.name}（active {act} / retired {ret}）")

    # 启动包是派生物：渲染完必须跟着刷新，否则"收尾了但包还是旧的"
    if not getattr(a, "no_pack", False):
        import subprocess
        for script in ("make_mode_pack.py",):
            sp = ROOT / "脚本" / script
            if not sp.exists():
                continue
            p = subprocess.run([sys.executable, str(sp)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if p.returncode == 0:
                for line in (p.stdout or "").strip().splitlines():
                    print(f"     {line}")
            else:
                print(f"[!] {script} 刷新失败：{(p.stderr or '').strip()[:200]}", file=sys.stderr)
                problems.append(f"启动包（{script}）")

    # 收尾自检0：日常原话清理（超过 60 天的整节移进归档）
    if not getattr(a, "no_check", False):
        dq = ROOT / "脚本" / "daily_quotes.py"
        if dq.exists():
            import subprocess
            q = subprocess.run([sys.executable, str(dq), "prune"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            out = (q.stdout or "").strip()
            if out and "无需清理" not in out:
                print(f"     [日常原话] {out.splitlines()[-1]}")

    # 收尾自检0·五：出差记忆轮转（活跃 3 天 + 所有 open；30 天前的月份压月包）
    if not getattr(a, "no_check", False):
        hf_script = ROOT / "脚本" / "handoff.py"
        if hf_script.exists():
            import subprocess as _sp
            q = _sp.run([sys.executable, str(hf_script), "rotate"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
            line = (q.stdout or "").strip().splitlines()
            if line and "移档 0" not in line[0]:
                print(f"     [出差记忆] {line[0]}")
            if q.returncode != 0:
                print(f"[!] 出差记忆轮转失败（rc={q.returncode}）", file=sys.stderr)
                problems.append("出差记忆轮转")

    # 收尾自检①：更新 L3 检索索引（FTS5，增量）
    if not getattr(a, "no_check", False):
        idx = ROOT / "脚本" / "l3_index.py"
        if idx.exists():
            import subprocess
            q = subprocess.run([sys.executable, str(idx), "build"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            line = (q.stdout or q.stderr or "").strip().splitlines()
            if line:
                print(f"     [检索索引] {line[-1]}")
            if q.returncode != 0:
                print(f"[!] 检索索引建失败（rc={q.returncode}）", file=sys.stderr)
                problems.append("检索索引")

    # 收尾自检②：这次会话有没有记出差记忆（没记只提醒，不拦）
    if not getattr(a, "no_check", False):
        from datetime import date, datetime as _dt
        hf = DATA / "记忆" / "L3" / "handoff"
        today = date.today()
        wrote = False
        if hf.exists():
            for f in hf.glob("20*.md"):
                if _dt.fromtimestamp(f.stat().st_mtime).date() == today:
                    wrote = True
                    break
        if wrote:
            print("     [handoff] 今天有记录")
        else:
            print("     [!] 今天还没有 handoff 记录——如果这次会话有事发生，记得 write_handoff",
                  file=sys.stderr)

    # 收尾自检③：L3 索引 <-> 文件是否对得上（只报，不拦渲染）
    if not getattr(a, "no_check", False):
        l3 = ROOT / "脚本" / "l3.py"
        if l3.exists():
            import subprocess
            q = subprocess.run([sys.executable, str(l3), "check"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            out = (q.stdout or "").strip()
            if q.returncode == 0:
                for line in out.splitlines():
                    print(f"     [L3] {line}")
            else:
                print("[!] L3 索引有问题：", file=sys.stderr)
                for line in out.splitlines():
                    print(f"     {line}", file=sys.stderr)
                problems.append("L3 一致性")


    if problems:
        print("", file=sys.stderr)
        print("[!] 收尾有子步骤失败：" + "、".join(problems), file=sys.stderr)
        print("    正文与包已经写好人能看，但**这些步骤没跑成功**，别当它绿了。",
              file=sys.stderr)
        print("    重跑：python 脚本/workbench.py render；单项排查见 her-doctor.py",
              file=sys.stderr)
        sys.exit(2)
    print("     [结论] 收尾全部步骤成功")


def cmd_import(a):
    """md → json。关键：**id 按文本锚定**，不按位置重排。

    md 的「已定」行不带 id，若按出现顺序发号，只要中间停用/新增一条，
    后面所有 id 都会整体挪位（D-03 变成 D-02），并和附录里已作废的 id 撞车。
    所以：文本能对上旧条目的，沿用旧 id（含 retired 的原 id/日期/理由）；
    只有全新文本才发新号（号码取全表最大值 +1）。
    """
    src = Path(a.md) if a.md else MD
    if not src.exists():
        sys.exit(f"找不到 {src}")
    text = src.read_text(encoding="utf-8")
    parsed = parse_md(text)

    arc_ok, arc_ids, why = archive_state()
    if ARC.exists() and not arc_ok and not getattr(a, "force", False):
        # 和 render 同一套校验：归档被动过就别导，否则"渲染按新数据、导入按旧数据"
        print(f"[X] 拒绝导入：{why}", file=sys.stderr)
        print("    先跑 python 脚本/wb_archive.py 重新生成归档；或加 --force 忽略", file=sys.stderr)
        sys.exit(3)
    if arc_ok and not getattr(a, "no_archive_aware", False):
        # 已归档条目：不进 decisions（归档文件才是它的家），也就不会被"未出现即保留"重新塞回
        parsed["decisions"] = [x for x in parsed["decisions"] if x["id"] not in arc_ids]

    if JS.exists():
        old = load()
        by_text = {x["text"]: dict(x) for x in old["decisions"]
                   if not (arc_ok and not getattr(a, "no_archive_aware", False)
                           and x["id"] in arc_ids)}
        merged, seen = [], set()
        by_id = {x["id"]: dict(x) for x in old["decisions"]}
        for x in parsed["decisions"]:
            keep = by_id.get(x.get("id", "")) or by_text.get(x.get("text", ""))
            if keep:
                if x["status"] == "retired":
                    keep["status"] = "retired"
                    keep["why"] = x.get("why") or keep.get("why", "")
                    keep["retired_at"] = x.get("retired_at") or keep.get("retired_at", "")
                else:
                    keep["status"] = "active"
                    if x.get("summary"):
                        keep["summary"] = x["summary"]
                    if x.get("pointer"):
                        keep["pointer"] = x["pointer"]
                    if not keep.get("text"):
                        keep["text"] = x.get("summary", "")
                item = keep
            else:
                item = x
            if item["id"] in seen:
                item = dict(item, id=next_id({"decisions": merged + old["decisions"]}))
            seen.add(item["id"])
            merged.append(item)
        # 老 json 里存在、md 里没出现的（例如双写期手改删了行）→ 保留，绝不丢
        parsed_ids = {x.get("id", "") for x in parsed["decisions"]}
        parsed_texts = {x.get("text", "") for x in parsed["decisions"]}
        for x in old["decisions"]:
            if x["id"] in parsed_ids or (x.get("text") and x["text"] in parsed_texts):
                continue   # md 里还在（按 id 认，指针表没有正文）
            if (arc_ok and not getattr(a, "no_archive_aware", False)
                    and x["id"] in arc_ids):
                merged.append(dict(x))   # 已归档：原样保留在 json，不参与 md 比对
            else:
                merged.append(dict(
                    x, status="retired",
                    why=x.get("why") or "导入时 md 中未出现（保留）",
                    retired_at=x.get("retired_at") or now()[:10],
                ))
        # 最后一道网：json 里有过、merged 里没有的，一律补回来（永不丢）
        have = {y.get("id") for y in merged}
        for x in old["decisions"]:
            if x.get("id") not in have:
                merged.append(dict(x))
        parsed["decisions"] = merged
        parsed["header"] = old.get("header") or parsed.get("header")
        parsed["mode"] = old.get("mode", "dual")
        parsed["client"] = old.get("client", "DSH")

    # id 去重：任何撞车（例如老附录行补号撞上现有 id）→ 重新发号，绝不让两件事共用一号
    fixed = []
    seen_ids = set()
    for x in parsed["decisions"]:
        if x["id"] in seen_ids:
            new_id = next_id({"decisions": fixed + parsed["decisions"]})
            print(f"[!] id 撞车：{x['id']} 已被占用，{x['text'][:16]}… 改为 {new_id}")
            x = dict(x, id=new_id)
        seen_ids.add(x["id"])
        fixed.append(x)
    parsed["decisions"] = fixed

    parsed["md_sha"] = sha(STAMP_RE.sub("", text))
    parsed["baseline"] = {"decisions": len(parsed["decisions"])}
    save(parsed)
    act = sum(1 for x in parsed["decisions"] if x.get("status") == "active")
    ret = len(parsed["decisions"]) - act
    print(f"[OK] 已从 {src.name} 生成 任务看板.json（active {act} / retired {ret} / blockers {len(parsed['blockers'])} / next {len(parsed['next'])}）")
    print("  双写期：md 仍可手改；改完跑 compare 看差异")


def cmd_compare(_):
    d = load()
    want = STAMP_RE.sub("", render_text(d))
    if not MD.exists():
        print("md 不存在")
        return
    have = STAMP_RE.sub("", MD.read_text(encoding="utf-8"))
    if want == have:
        print("[OK] 一致（json 渲染 == 现 md，忽略最后更新行）")
        return
    import difflib
    diff = list(difflib.unified_diff(have.splitlines(), want.splitlines(),
                                     fromfile="现 md", tofile="json 渲染", lineterm=""))
    print("\n".join(diff[:80]))
    print(f"\n差异 {len(diff)} 行（>0 说明两边不一致）")


def cmd_status(_):
    d = load()
    print(f"模式：{d.get('mode')}｜更新：{d.get('updated')}｜客户端：{d.get('client')}")
    print(f"现在：{d.get('now')}")
    print(f"已定：active {sum(1 for x in d['decisions'] if x.get('status')=='active')} / "
          f"retired {sum(1 for x in d['decisions'] if x.get('status')!='active')}")
    print(f"卡在哪 {len(d['blockers'])}｜下一步 {len(d['next'])}｜坑 {len(d.get('pitfalls', []))}")
    if MD.exists() and d.get("md_sha"):
        edited = sha(STAMP_RE.sub("", MD.read_text(encoding="utf-8"))) != d["md_sha"]
        print(f"md 状态：{'[!] 被手改过（未渲染）' if edited else '与 json 同步'}")


def cmd_set_now(a):
    d = load(); d["now"] = a.text; d["client"] = a.client or d.get("client", "DSH"); save(d)
    print(f"[OK] 现在 = {a.text}")


def cmd_decide(a):
    d = load()
    i = next_id(d)
    d["decisions"].append({"id": i, "text": a.text, "pointer": a.pointer or "",
                           "date": now()[:10], "status": "active"})
    save(d)
    print(f"[OK] 已定 +{i}：{a.text}")


def cmd_retire(a):
    d = load()
    hit = next((x for x in d["decisions"] if x["id"] == a.id), None)
    if not hit:
        sys.exit(f"没有 {a.id}")
    hit["status"] = "retired"
    hit["why"] = a.why
    hit["retired_at"] = now()[:10]
    by = getattr(a, "by", None)
    if by:
        if not any(x["id"] == by and x.get("status") == "active" for x in d["decisions"]):
            sys.exit(f"--by {by} 不存在，或者它自己也是作废的；替代链只能指向活着的条目")
        hit["replaced_by"] = by
    save(d)
    tail = f"，由 {by} 接" if by else "（没写替代，替代栏会空着）"
    print(f"[OK] {a.id} 已作废（留在附录，不删）：{a.why}{tail}")


def cmd_pitfall(a):
    d = load()
    d.setdefault("pitfalls", []).append({"text": a.text, "date": now()[:10]})
    save(d)
    print(f"[OK] 记坑：{a.text}")


def cmd_blocker(a):
    d = load()
    if a.action == "clear":
        d["blockers"] = []
        print("[OK] 卡在哪已清空")
    else:
        d.setdefault("blockers", []).append(a.text)
        print(f"[OK] 卡在哪 +1：{a.text}")
    save(d)


def cmd_next(a):
    d = load()
    if a.action == "clear":
        d["next"] = []
        print("[OK] 下一步已清空")
    else:
        d["next"] = a.items
        print(f"[OK] 下一步 = {len(a.items)} 条")
    save(d)


def cmd_merge(a):
    """收尾用：把一份 md 交接单并进 json。
    替换：现在 / 卡在哪 / 下一步；追加：已定、坑（按文本去重，绝不删）。"""
    src = Path(a.md)
    if not src.exists():
        sys.exit(f"找不到 {src}")
    incoming = parse_md(src.read_text(encoding="utf-8"))
    d = load()
    d["now"] = incoming["now"] or d.get("now", "")
    d["blockers"] = incoming["blockers"]
    d["next"] = incoming["next"]
    have = {x["text"] for x in d["decisions"]}
    added = 0
    for x in incoming["decisions"]:
        if x["text"] not in have:
            i = next_id(d)
            d["decisions"].append({"id": i, "text": x["text"], "pointer": "",
                                   "date": now()[:10], "status": "active"})
            have.add(x["text"]); added += 1
    by_id = {x["id"]: x for x in d["decisions"]}
    for x in incoming["decisions"]:
        if x.get("status") != "retired":
            continue
        if x["id"] not in by_id:
            d["decisions"].append(x)
            by_id[x["id"]] = x
            added += 1
    havep = {x["text"] for x in d.get("pitfalls", [])}
    for x in incoming.get("pitfalls", []):
        if x["text"] not in havep:
            d.setdefault("pitfalls", []).append(x); added += 1
    save(d)
    print(f"[OK] 已并入：现在/卡在哪/下一步 已替换；新增 {added} 条（已定/坑只追不删）")
    print("  下一步跑 render 刷新 md")


def cmd_flip(_):
    d = load()
    d["mode"] = "json"
    save(d)
    print("[OK] 已切到 json 单源：md 从此为只读视图（手改会被拦）")


def main():
    ap = argparse.ArgumentParser(description="任务看板（真源 json + 渲染 md）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("import-md"); p.add_argument("--md")
    p.add_argument("--no-archive-aware", action="store_true",
                   help="忽略归档，照旧把 md 里没出现的条目全部保留（排障用）")
    p.add_argument("--force", action="store_true", help="无视归档 sha 不符，强行导入（排障用）")
    p.set_defaults(fn=cmd_import)
    sub.add_parser("compare").set_defaults(fn=cmd_compare)
    p = sub.add_parser("render"); p.add_argument("--force", action="store_true")
    p.add_argument("--why", default="")
    p.add_argument("--no-pack", action="store_true", help="只渲染 md，不刷新启动包")
    p.add_argument("--no-check", action="store_true", help="跳过 L3 索引校验")
    p.add_argument("--no-archive", action="store_true", help="不自动同步任务看板归档")
    p.set_defaults(fn=cmd_render)
    p = sub.add_parser("set-now"); p.add_argument("text"); p.add_argument("--client"); p.set_defaults(fn=cmd_set_now)
    p = sub.add_parser("decide"); p.add_argument("text"); p.add_argument("--pointer")
    p.add_argument("--expires", default="")
    p.set_defaults(fn=cmd_decide)
    sub.add_parser("prune").set_defaults(fn=cmd_prune)
    p = sub.add_parser("keep"); p.add_argument("id"); p.add_argument("--why", required=True); p.set_defaults(fn=cmd_keep)
    p = sub.add_parser("retire"); p.add_argument("id"); p.add_argument("--why", required=True); p.add_argument("--by"); p.set_defaults(fn=cmd_retire)
    p = sub.add_parser("pitfall"); p.add_argument("text"); p.set_defaults(fn=cmd_pitfall)
    p = sub.add_parser("blocker"); p.add_argument("action", choices=["add", "clear"]); p.add_argument("text", nargs="?"); p.set_defaults(fn=cmd_blocker)
    p = sub.add_parser("next"); p.add_argument("action", choices=["set", "clear"]); p.add_argument("items", nargs="*"); p.set_defaults(fn=cmd_next)
    p = sub.add_parser("merge-md"); p.add_argument("--md", required=True); p.set_defaults(fn=cmd_merge)
    sub.add_parser("flip").set_defaults(fn=cmd_flip)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    main()
