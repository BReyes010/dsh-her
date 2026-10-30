#!/usr/bin/env python3
"""her-host：她的入口（MCP stdio 服务）。

设计原则：
  - **只读 L0/L1/L2**，写入只能进待确认区（stage_memory）
  - **永不暴露 L3**（不提供任何读原文的工具）
  - 任务看板（入口/任务看板.md）用于跨客户端交接，不算记忆、不需要签字
  - 零依赖：只用标准库，JSON-RPC over stdio（一行一个 JSON 对象）

用法（клиент 配置里）：
  { "mcpServers": { "dafeiyu": {
      "command": "<python.exe>",
      "args": ["D:\\...\\人格与记忆\\入口\\her-host.py"]
  } } }

手工调试：
  echo {"jsonrpc":"2.0","id":1,"method":"initialize","params":{}} | python her-host.py
"""

import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os as _os
DATA = Path(_os.environ['HER_HOME']).resolve() if _os.environ.get('HER_HOME') else ROOT
SCRIPTS = ROOT / "脚本"
sys.path.insert(0, str(ROOT / "脚本"))
from paths import paths as _paths  # noqa: E402
_P = _paths()
_SC = _P["scenes"]
PERSONA = Path(_SC["日常"][0])
WORK = Path(_SC["口播"][1]) if len(_SC.get("口播", [])) > 1 else PERSONA
TECH = Path(_SC["口播"][2]) if len(_SC.get("口播", [])) > 2 else PERSONA
STARTUP = _P["startup"]
DESK = _P["desk"]
MEMDB = _P["db"]
L1DB = _P["l1"]
PENDING_CLOSE = DATA / "记忆" / "L3" / "handoff" / "_待补.json"

# 当前会话模式：her-host 是长驻进程，以**最近一次 get_persona 的 scene** 为准
CURRENT = {"scene": "未知"}   # 只作提示与错误文案；**写权判据是签发**（见 规格/写权-契约.md）
# 只有完整模式能直接落盘；投影（日常/工作）只能读、提案、留待补
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "脚本"))
try:
    import policy as _policy
except Exception:
    _policy = None


def _simple():
    return bool(_policy and _policy.is_simple())


WRITE_TOOLS = {"write_handoff", "note_add", "set_snapshot", "note_daily", "wb_retire"}
PROJECTION = {"日常", "工作"}

# 工具 → 需要的签发项
GRANT_OF = {
    "note_add": "write:memory",
    "note_daily": "write:memory",
    "write_handoff": "write:handoff",
    "set_snapshot": "write:state",
    "wb_retire": "write:state",
}
GRANT_FILE = DATA / "入口" / "_签发.json"
# 模型申请取卡的会话态（额度见 规格/写权-契约.md §9）
CARD_STATE = {"requests": 0, "changes": 0, "denied": set(), "loaded": [], "last": {}}
ASSEMBLY_LOG = DATA / "记忆" / "L3" / "_装配.jsonl"
REQ_MAX, CHG_MAX, COOLDOWN_S = 2, 4, 600


def _assembly_log(rec):
    import json as _j
    from datetime import datetime as _d
    rec = {"ts": _d.now().strftime("%Y-%m-%d %H:%M"), **rec}
    try:
        ASSEMBLY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ASSEMBLY_LOG.open("a", encoding="utf-8") as f:
            f.write(_j.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _cards():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import pack_assembler as pa
        return pa.load_cards()
    except Exception as e:
        raise RuntimeError(f"卡目录读不出：{e}")

GRANT_LOG = DATA / "记忆" / "L3" / "_签发.jsonl"


def _grant_log(rec):
    """系统侧记账：不依赖 token；写不成就抛（调用方据此拒写）。"""
    GRANT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with GRANT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _active_grants():
    if not GRANT_FILE.exists():
        return []
    try:
        d = json.loads(GRANT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    from datetime import datetime as _dt
    out = []
    for g in d.get("grants", []):
        if g.get("revoked") or g.get("consumed"):
            continue
        try:
            if _dt.strptime(g["expires"], "%Y-%m-%d %H:%M") > _dt.now():
                out.append(g)
        except Exception:
            continue
    return out


def _approve_add(tool, need, why):
    """被"没票"拒时，记进待批准队列（maker 点一下就签）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "脚本"))
        import approve as _ap
        return _ap.add(tool, need, why, CURRENT.get("scene", ""))
    except Exception:
        return ""


def deny_reason(tool):
    """写工具的四步闸：① 记尝试 ② 校验签发 ③（调用方）执行 ④ 记结果。

    本函数只做 ①②④-失败分支；执行成功由调用方调 grant_ok() 落 ④。
    """
    if tool not in WRITE_TOOLS:
        return ""

    # 2026-10-29：任务看板免票（三行 + 退役带理由）；简易审核免签（但留审计）
    if tool in ("set_snapshot", "wb_retire"):
        return ""
    _need0 = GRANT_OF.get(tool, "")
    if _policy is not None and _policy.is_simple() and _need0 in ("write:memory", "write:handoff"):
        try:
            from datetime import datetime as _d0
            _grant_log({"ts": _d0.now().strftime("%Y-%m-%d %H:%M"),
                        "event": "simple-auto", "tool": tool, "need": _need0,
                        "scene": CURRENT.get("scene", ""),
                        "note": "简易审核：免签自动放行"})
        except Exception:
            pass
        return ""

    from datetime import datetime as _dt
    ts = _dt.now().strftime("%Y-%m-%d %H:%M")
    need = GRANT_OF.get(tool, "")
    rec = {"ts": ts, "event": "attempt", "tool": tool, "need": need,
           "scene": CURRENT["scene"], "session": CURRENT.get("session", "auto")}
    try:
        _grant_log(rec)                      # ① 先记尝试（无签发也照记）
    except Exception as e:
        return f"拒绝：审计通道写不进去（{e}），这次写操作不做。"

    ok = [g for g in _active_grants() if need in g.get("tools", [])]

    # ② 校验
    reasons = []
    if not ok:
        if CURRENT["scene"] == "未知":
            reasons.append("还没确认这次是什么模式")
        if CURRENT["scene"] in PROJECTION:
            reasons.append(f"当前是「{CURRENT['scene']}投影」")
        reasons.append(f"没有有效的写权签发（需要 {need}）")
        msg = ("拒绝：" + "；".join(reasons) + "。"
               "写权按「哪一次签发」判，不按机器或模式；"
               f"请让 maker 跑 `python 脚本/grant.py sign --tools {need} --reason \"…\"`，"
               "或先用 mark_close_pending 留一行待补。")
        try:
            _grant_log({**rec, "event": "denied", "code": "E_NO_GRANT"})   # ④ 失败也记
        except Exception:
            pass
        aid = _approve_add(tool, need, f"{tool} 被拒：需要 {need}")
        if aid:
            msg += f"（已进待批准：{aid}——maker 点一下「审批」就等于签票）"
        return msg
    CURRENT["grant"] = ok[0]["id"]
    return ""


def grant_ok(tool, extra=""):
    """④ 结果：成功路径也落账；**单次票用掉即作废**（2026-10-29）。"""
    if tool not in WRITE_TOOLS:
        return
    import json as _j
    from datetime import datetime as _dt
    ts = _dt.now().strftime("%Y-%m-%d %H:%M")
    gid = CURRENT.get("grant", "")
    try:
        _grant_log({"ts": ts, "event": "allowed", "tool": tool, "grant": gid,
                    "scene": CURRENT["scene"], "extra": extra})
    except Exception:
        pass
    # 单次票：标记 consumed（宿主直写，不经她的写权）
    if not gid or not GRANT_FILE.exists():
        return
    try:
        d = _j.loads(GRANT_FILE.read_text(encoding="utf-8"))
        for x in d.get("grants", []):
            if x.get("id") == gid and int(x.get("uses") or 0) > 0:
                left = int(x.get("uses") or 0) - 1
                x["uses"] = left
                if left == 0:
                    x["consumed"] = True
                    x["consumed_at"] = ts
                GRANT_FILE.write_text(_j.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                _grant_log({"ts": ts, "event": "consumed", "grant": gid, "tool": tool})
                CURRENT.pop("grant", None)
                break
    except Exception:
        pass

SERVER_INFO = {"name": "her-host", "version": "0.1.0"}
PROTOCOL = "2024-11-05"


# ---------------- 读数 ----------------

def read(path, fallback=""):
    """读进来准备注入的文本。

    2026-10-29：**必须剥掉 HTML 注释行**——派生文件里带「由 … 生成，勿手改」这类
    元信息，那是我给她看的规矩，不是她该读到的内容（会让她开始解释自己的文件）。
    """
    if not path.exists():
        return fallback
    import re as _re
    txt = path.read_text(encoding="utf-8")
    return _re.sub(r"(?m)^\s*<!--.*?-->\s*$\n?", "", txt)


def l1_block():
    if not L1DB.exists():
        return ""
    try:
        con = sqlite3.connect(L1DB)
        rows = con.execute("SELECT rule FROM l1 WHERE state IN('active','protected') ORDER BY id").fetchall()
        con.close()
    except sqlite3.OperationalError:
        return ""  # 套件还没建 l1 表 → 当作空
    if not rows:
        return ""
    return "## 自我认知（她学会的事）\n" + "\n".join(f"- {r[0]}" for r in rows)


def memory_block(scene="工作"):
    """常驻层 + 场景过滤后的条目摘要（只取常驻层，关键词层交给 search_memory）。"""
    if not MEMDB.exists():
        return ""
    con = sqlite3.connect(MEMDB)
    rows = con.execute(
        "SELECT num,title,raw,scene FROM entries WHERE constant=1 AND enabled=1 AND pending=0 "
        "ORDER BY insert_order, num").fetchall()
    con.close()
    out = []
    for num, title, raw, sc in rows:
        # 通用 = 不过滤（日常 + 工作的记忆都要有）；其余按"通用 + 本场景"
        if scene != "完整" and sc not in ("通用", scene):
            continue
        body = raw.split("】", 1)[-1].strip() if "】" in raw else raw
        out.append(body)

    text = "\n\n".join(out)
    if scene == "完整":
        # 完整模式：常驻层之外，其余条目各给一行索引（日常 + 工作都在）
        con = sqlite3.connect(MEMDB)
        con.row_factory = sqlite3.Row
        rest = con.execute(
            "SELECT num,title,scene,raw FROM entries WHERE constant=0 AND enabled=1 AND pending=0 "
            "ORDER BY insert_order, num").fetchall()
        con.close()
        if rest:
            blocks = []
            for r in rest:
                b = r["raw"].split("】", 1)[-1].strip()
                blocks.append(f"【{r['title']}】（场景：{r['scene']}，#{r['num']}）\n{b}")
            text += ("\n\n" + "\n\n".join(blocks))
    return text


def desk_snapshot():
    """读任务看板；去掉首行 H1（外层已有「## 任务看板」标题，避免双标题）。"""
    text = read(DESK, "（任务看板为空）")
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def desk_now_line():
    """只取任务看板「现在」这一段（给工作模式用，不带项目周报）。"""
    text = read(DESK, "")
    lines = text.splitlines()
    out, grab = [], False
    for ln in lines:
        if ln.startswith("## "):
            grab = ln.startswith("## 现在")
            continue
        if grab and ln.strip():
            out.append(ln.strip())
    return "\n\n".join(out)



def desk_now_block():
    """日常模式专用：只给一行「你现在在哪一步」。

    这是 2026-10-28 的定案——日常读整张任务看板会滑成汇报，所以只给一行，
    且这一行只回答"我卡在哪一步"，不是话头。
    不合格（太长 / 含数值 / 含编号）就**不注入**，不截半句、不补占位。
    """
    js = DESK.parent / "任务看板.json"
    if not js.exists():
        return ""
    try:
        d = json.loads(js.read_text(encoding="utf-8-sig"))
    except Exception:
        return ""
    now = (d.get("now") or "").strip()
    if not now:
        return ""

    han = sum(1 for c in now if "\u4e00" <= c <= "\u9fff")
    if han > 40:
        print(f"[warn] 日常「现在」一行 {han} 汉字，超过 40，本次不注入", file=sys.stderr)
        return ""
    bad = re.search(r"播放|点赞|硬币|涨粉|粉丝数|时长|字数|\d+\s*[句字]|D-\d+", now)
    if bad:
        print(f"[warn] 日常「现在」一行含禁用词「{bad.group(0)}」，本次不注入", file=sys.stderr)
        return ""

    upd = (d.get("updated") or "").strip()
    lines = ["## 你现在在哪一步", "", now]
    stale = False
    if upd:
        lines.append(f"（{upd}）")
        try:
            t = datetime.strptime(upd[:16], "%Y-%m-%d %H:%M")
            stale = (datetime.now() - t).total_seconds() > 48 * 3600
        except Exception:
            stale = False
    if stale:
        lines.append("（这行可能过期了，别当成现在）")
    lines += ["", "只用于被问到时回答，不是话头。"]
    return "\n".join(lines)

PROJ_LINE = (
    "这一趟我是投影：读得到，写不了。收尾/笔记/改任务看板都归完整模式；"
    "我可以提交待确认条目（要 maker 签字）。"
    "被问「上回做到哪」，只答手里这段，查不到就说查不到；想收尾就用 `mark_close_pending` 留一行。"
)


SESSION_LOG = ROOT / "记忆" / "L3" / "_会话.jsonl"


def _rel_day(d):
    """今天/昨天/N 天前。"""
    from datetime import date
    today = date.today()
    delta = (today - d).days
    if delta <= 0:
        from datetime import datetime
        hh = datetime.now().hour
        return "今天上午" if hh < 12 else ("今天下午" if hh < 18 else "今天晚上")
    if delta == 1:
        return "昨天"
    if delta < 7:
        return f"{delta} 天前"
    return None


def today_block():
    """日常模式的时间锚：今天是几号 + 上一次聊天（只读 _会话.jsonl）。"""
    from datetime import date, datetime
    WEEK = "一二三四五六日"
    now = date.today()
    head = f"今天是 {now.isoformat()}（周{WEEK[now.weekday()]}）"
    last = None
    if SESSION_LOG.exists():
        try:
            lines = [l for l in SESSION_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
            if lines:
                rec = json.loads(lines[-1])
                last = datetime.strptime(rec["ts"][:10], "%Y-%m-%d").date()
        except Exception:
            last = None
    if last:
        rel = _rel_day(last)
        tail = f"上一次聊天：{rel}。" if rel else f"上一次聊天：{last.isoformat()}（有点久了）。"
    else:
        tail = "上一次聊天：没有记录。"
    return "## 现在几号\n\n" + head + "。" + tail



def last_quotes_block():
    """日常模式：上一次聊天说过的三句（只读 日常原话.md 最近一节）。"""
    f = ROOT / "记忆" / "日常原话.md"
    if not f.exists():
        return ""
    try:
        text = f.read_text(encoding="utf-8")
    except Exception:
        return ""
    secs = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^## (\d{4}-\d{2}-\d{2})\s*$", line)
        if m:
            cur = m.group(1)
            secs[cur] = []
        elif cur and line.strip().startswith("- "):
            secs[cur].append(line.strip())
    if not secs:
        return ""
    last_date = sorted(secs)[-1]
    lines = [l for l in secs[last_date] if l.strip()][-3:]
    if not lines:
        return ""
    return "## 上一次聊天，说过的三句\n\n" + "\n".join(lines)


def build_persona(scene="工作", desk="full"):
    parts = [read(PERSONA), ""]
    if scene in ("工作", "完整"):
        # 通用 = 完整角色：技能（场景规程）也全装
        parts += [read(WORK), "", read(TECH), ""]
    # 出差记忆（T1）：只进工作模式。日常模式只聊天，不聊工作——
    # 2026-10-27 maker 定：日常注入里不许出现任务看板、口播、出差日志一类的东西。
    if scene != "日常":
        try:
            sys.path.insert(0, str(SCRIPTS))
            import handoff as hf
            t1 = hf.t1_block()
            if t1:
                parts += [t1, ""]
        except Exception:
            pass
    l1 = l1_block()
    if l1:
        parts += [l1, ""]
    mem = memory_block(scene)
    if mem:
        head = "## 我记得的事" if scene == "完整" else "## 我记得的事（常驻层）"
        parts += [head, mem, ""]
    if scene == "日常":
        parts += [today_block(), ""]
        qb = last_quotes_block()
        if qb:
            parts += [qb, ""]
    if scene == "日常":
        parts += ["## 这一趟我是投影", "", PROJ_LINE, ""]
    elif scene == "工作":
        # 工作包体积紧：投影自述压成一行，省 ~70 汉字
        parts += ["（投影：只读、不记事；收尾与改任务看板归完整模式。）", ""]
    if desk == "full":
        parts += ["## 任务看板（上一次会话干到哪）", desk_snapshot()]
    elif desk == "now":
        blk = desk_now_block()
        if blk:
            parts += [blk, ""]
    elif desk == "brief":
        brief = desk_now_line()
        if brief:
            parts += ["## 这一期在做什么", brief]
    return "\n".join(parts)


# ---------------- 检索 ----------------

def search_memory(topic, scene="工作", include_pending=False):
    sys.path.insert(0, str(SCRIPTS))
    import memory_lookup as ml  # noqa: E402
    if not ml.BOOK.exists():
        return "（记忆库不存在）"
    entries = ml.parse(ml.BOOK.read_text(encoding="utf-8"))
    if not include_pending:
        entries = [e for e in entries if not e.pending]
    entries = [e for e in entries if e.enabled]
    if scene != "通用":
        entries = [e for e in entries if e.scene in ("通用", scene)]
    topics = [topic] if isinstance(topic, str) and topic else list(topic or [])
    const = [e for e in entries if e.constant]
    hits = [(e, e.matches(topics)) for e in entries if not e.constant and e.matches(topics)]
    return ml.render(const + [e for e, _ in hits], topics)


# ---------------- 写（只进待确认区） ----------------

def stage_memory(title, category, body, keys="", scene="通用", note=""):
    import argparse
    import subprocess
    args = [sys.executable, str(SCRIPTS / "memory_db.py"), "add",
            "--title", title, "--category", category, "--body", body,
            "--scene", scene, "--source", "reflection", "--note", note or "her-host 提交"]
    if keys:
        args += ["--keys", keys]
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    return (p.stdout or p.stderr).strip()


def write_snapshot(content, client="unknown"):
    """收尾写任务看板：走 workbench.merge-md —— 替换「现在/卡在哪/下一步」，
    「已定」只追加、绝不删除；然后 render。不再整份覆盖。"""
    import subprocess
    import tempfile
    old = read(DESK, "")
    if old:
        (DESK.parent / "任务看板.bak.md").write_text(old, encoding="utf-8")

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = f"# 任务看板（跨客户端交接单）\n\n最后更新：{stamp} · 客户端：{client}\n\n{content.strip()}\n"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(payload)
        tmp = f.name
    try:
        merge = subprocess.run([sys.executable, str(SCRIPTS / "workbench.py"), "merge-md", "--md", tmp],
                               capture_output=True, text=True, encoding="utf-8")
        if merge.returncode != 0:
            print(f"TROUBLE merge-md rc={merge.returncode}: {merge.stderr[:200]}", file=sys.stderr)
        ren = subprocess.run([sys.executable, str(SCRIPTS / "workbench.py"), "render"],
                             capture_output=True, text=True, encoding="utf-8")
        if ren.returncode != 0:
            print(f"TROUBLE render rc={ren.returncode}: {ren.stderr[:200]}", file=sys.stderr)
    finally:
        Path(tmp).unlink(missing_ok=True)

    tail = (merge.stdout or "").strip().splitlines()
    head = (ren.stdout or "").strip().splitlines()
    summary = "；".join(x for x in [tail[0] if tail else "", head[0] if head else ""] if x)
    return f"任务看板已更新（{stamp}，客户端 {client}）｜{summary}"


def scan_text(text):
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp = f.name
    p = subprocess.run([sys.executable, str(SCRIPTS / "scan.py"), tmp],
                       capture_output=True, text=True, encoding="utf-8")
    Path(tmp).unlink(missing_ok=True)
    return (p.stdout or "").strip()


# ---------------- MCP 工具表 ----------------

TOOLS = [
    {
        "name": "get_persona",
        "description": "取她这次要注入的全部内容（人格内核 + 场景材料 + L1 + 常驻记忆 + 任务看板）。开场第一步调它。",
        "inputSchema": {
            "type": "object",
            "properties": {"scene": {"type": "string", "enum": ["日常", "工作", "完整"], "default": "工作"}},
        },
    },
    {
        "name": "search_memory",
        "description": "按主题检索记忆（常驻层 + 关键词命中 + 场景过滤）。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "array", "items": {"type": "string"}},
                "scene": {"type": "string", "enum": ["日常", "工作", "通用"], "default": "工作"},
                "include_pending": {"type": "boolean", "default": False},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "stage_memory",
        "description": "把一条新记忆送进待确认区（不会立刻生效，要 maker 签字）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"}, "category": {"type": "string"},
                "body": {"type": "string"}, "keys": {"type": "string"},
                "scene": {"type": "string", "enum": ["通用", "日常", "工作"], "default": "通用"},
                "note": {"type": "string"},
            },
            "required": ["title", "category", "body"],
        },
    },
    {
        "name": "note_add",
        "description": "往她自己的笔记里追加一条：kind=触动（打动过她的一句话，必须给出处与触动点）"
                       "或 kind=项目（新建一个项目思考骨架）。只追加、不改写。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["触动", "项目"]},
                "text": {"type": "string", "description": "触动：原话（≤120 汉字）；项目：一句话说明要解决什么"},
                "source": {"type": "string", "description": "触动必填：谁说的 / 在哪看到"},
                "why": {"type": "string", "description": "触动必填：为什么触动"},
                "name": {"type": "string", "description": "项目名（kind=项目 时必填）"},
            },
            "required": ["kind", "text"],
        },
    },
    {
        "name": "note_daily",
        "description": "收尾时记「上一次聊天说过的几句原话」（最多 3 句、每句 ≤40 汉字）。"
                       "只记原话，不摘要；不记工作内容。日常包开场会带出最近这三句。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "who": {"type": "string", "enum": ["他", "我", "她"]},
                "text": {"type": "string", "description": "原话，逐字"},
            },
            "required": ["who", "text"],
        },
    },
    {
        "name": "recall",
        "description": "查 L3 永久记忆（原始日志）：用「大致时间 + 关键词」找很久以前发生过什么。"
                       "只回片段（≤3 条、每条 ≤300 字、带出处），仅用于这一轮回答，**不写回记忆**。"
                       "查不到会说「没找到」；线索太少会让你先问用户。三档模式都可用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户原话，尽量别改写"},
                "when": {"type": "string", "description": "大致时间：上周 / 10 月中 / 最近三天 / 第 2 期"},
                "keywords": {"type": "string", "description": "关键词，空格分隔"},
                "budget": {"type": "integer", "description": "探针次数，默认 3", "default": 3},
            },
            "required": ["question"],
        },
    },
    {
        "name": "mark_close_pending",
        "description": "投影模式（日常/工作）里想收尾但没有写权限时用：留一行「待补」，"
                       "完整模式下次开场 preflight 会看到，补写完自动清空。只写这一行字，不写 handoff、不改任务看板。",
        "inputSchema": {
            "type": "object",
            "properties": {"line": {"type": "string", "description": "一句话：这轮做了什么、下次要补什么"}},
            "required": ["line"],
        },
    },
    {
        "name": "preflight",
        "description": "开场自检：上次收尾有没有写进 L3、这次要加载的东西对不对。"
                       "用户说「继续」或开新对话时先跑它；有警告就照实说，不要跳过。",
        "inputSchema": {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["日常", "工作", "完整"]}},
        },
    },
    {
        "name": "search_notes",
        "description": "查她自己的两份笔记：触动（打动过她的话）与项目（大项目的思路）。"
                       "这两份是她自己挑过的素材，**不需要判断该不该查**，直接查即可；"
                       "不要用它查原始日志（期/handoff/归档）。每次调用都会落一行审计。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["触动", "项目"], "description": "查哪份"},
                "query": {"type": "string", "description": "关键词，可空格分隔多个（全部命中）"},
            },
            "required": ["kind", "query"],
        },
    },
    {
        "name": "get_snapshot",
        "description": "读任务看板：上一次会话干到哪（已定/未决/下一步）。用户说「继续」时先调它；读到的东西只用于这一轮，不要写回记忆。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_snapshot",
        "description": "写任务看板（收尾时调用）：只写结论，不写过程。会自动备份上一版。",
        "inputSchema": {
            "type": "object",
            "properties": {"content": {"type": "string"}, "client": {"type": "string"}},
            "required": ["content"],
        },
    },
    {
        "name": "write_handoff",
        "description": "收尾时写入差记忆：一次会话一个文件，1-3 条主题条目。L3 原文永久保存。",
        "inputSchema": {
            "type": "object",
            "properties": {"client": {"type": "string"}, "content": {"type": "string"}},
            "required": ["client", "content"],
        },
    },
    {
        "name": "wb_retire",
        "description": "把任务看板里一条已定「退休」（不是删除）：落进 记忆/L3/归档/任务看板-已作废.md，"
                       "之后仍可用 wb_query 按编号查到。只在完整模式可用。"
                       "用于一次性事实（已发布/已交付）或纯指针条目；"
                       "**不要**拿它删条目，也别退行为约束类（不许/必须/一律）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "条目编号，如 D-23"},
                "why": {"type": "string", "description": "为什么退（会写进归档，必填）"},
                "by": {"type": "string", "description": "被谁取代（如 D-33，可空）"},
            },
            "required": ["id", "why"],
        },
    },
    {
        "name": "list_t1",
        "description": "读 T1：上一次会话的条目 + 所有未解决的 open 条目（超 3 天会标旧）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "request_card",
        "description": "申请装入一张技能/记忆卡（只申请，不自装）。每会话最多 2 次、每次 1 张；"
                       "被拒后同一张卡本会话不得再申请；同卡 10 分钟冷却。需说明为什么需要。",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "why": {"type": "string"}},
            "required": ["id", "why"],
        },
    },
    {
        "name": "unload_card",
        "description": "卸掉一张已装的卡（不需要理由）。计入变更次数（≤4/会话），不占申请额度。",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "l1_candidate",
        "description": "提一条「自我认知」候选（L1）。只进候选区，不占 10 行、不注入，要 maker 签字才生效。"
                       "证据至少两条，可用 期号/会话#<会话名>/笔记#<关键词>。"
                       "改变做法写 impact_kind=做法；只是「遇到这种场合会停一下」写 反应。"
                       "实在指不出做法，用 honest=true 说「先放着」——放着不丢人，编才丢人。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule": {"type": "string", "description": "一句话原则"},
                "refs": {"type": "string", "description": "证据，逗号分隔：会话#20261028-2032-DSH, 笔记#扩散"},
                "why": {"type": "string", "description": "为什么立这条"},
                "impact_kind": {"type": "string", "enum": ["做法", "反应"]},
                "impact": {"type": "string", "description": "改变的具体做法/反应（honest 时可空）"},
                "reclaim": {"type": "string", "description": "什么情况下该收回这条"},
                "occasion": {"type": "string",
                             "description": "来源域，自由填（工作/日常/娱乐/游戏/学习/社交/其它）"},
                "trigger": {"type": "string", "description": "触发条件：什么信号出现时生效"},
                "observable": {"type": "string", "description": "可观察差异：别人能看出什么不同"},
                "wrong_if_missing": {"type": "string", "description": "不遵守会出什么具体错"},
                "honest": {"type": "boolean", "description": "指不出做法，先放着"},
            },
            "required": ["rule", "refs", "why", "reclaim", "occasion"],
        },
    },
    {
        "name": "scan_draft",
        "description": "扫描一篇口播稿（自称/标点/数字/句长/直播腔/计数位/一行一句）。退出码 1 = 有违规。",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
]

RESOURCES = [
    {"uri": "persona://core", "name": "人格内核", "mimeType": "text/markdown"},
    {"uri": "persona://work", "name": "口播投影", "mimeType": "text/markdown"},
    {"uri": "persona://tech", "name": "口播技术规范", "mimeType": "text/markdown"},
    {"uri": "persona://startup", "name": "启动文档", "mimeType": "text/markdown"},
    {"uri": "persona://desk", "name": "任务看板", "mimeType": "text/markdown"},
]


def call_tool(name, args):
    args = args or {}
    reason = deny_reason(name)          # ①②（含失败分支的 ④）
    if reason:
        return reason
    out = _call_tool(name, args)        # ③ 执行
    if name in WRITE_TOOLS and not str(out).startswith("拒绝"):
        grant_ok(name, str(out)[:80])   # ④ 成功也落账
    return out


def _call_tool(name, args):
    if name == "get_persona":
        scene = args.get("scene", "工作")
        CURRENT["scene"] = scene          # 记住这次会话是什么模式
        return build_persona(scene)
    if name == "search_memory":
        return search_memory(args.get("topic"), args.get("scene", "工作"),
                             args.get("include_pending", False))
    if name == "stage_memory":
        return stage_memory(args["title"], args["category"], args["body"],
                            args.get("keys", ""), args.get("scene", "通用"), args.get("note", ""))
    if name == "note_add":
        kind = args.get("kind") or ""
        text = (args.get("text") or "").strip()
        if kind not in ("触动", "项目"):
            return "kind 只能是 触动 / 项目"
        if not text:
            return "text 不能为空"
        cmd = [sys.executable, str(SCRIPTS / "notes.py"), "add", "--kind", kind, "--text", text]
        if kind == "触动":
            cmd += ["--source", args.get("source") or "", "--why", args.get("why") or ""]
        else:
            cmd += ["--name", args.get("name") or ""]
        import subprocess
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        return (r.stdout or r.stderr).strip()
    if name == "note_daily":
        import subprocess
        r = subprocess.run([sys.executable, str(SCRIPTS / "daily_quotes.py"), "add",
                            "--who", args.get("who") or "他", "--text", args.get("text") or ""],
                           capture_output=True, text=True, encoding="utf-8")
        return (r.stdout or r.stderr).strip()
    if name == "recall":
        q = (args.get("question") or "").strip()
        if not q:
            return "question 不能为空"
        import subprocess
        cmd = [sys.executable, str(SCRIPTS / "l3q.py"), "recall", "--question", q,
               "--budget", str(args.get("budget") or 3)]
        if args.get("when"):
            cmd += ["--when", args["when"]]
        if args.get("keywords"):
            cmd += ["--keywords", args["keywords"]]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        return (r.stdout or r.stderr).strip()
    if name == "mark_close_pending":
        line = (args.get("line") or "").strip()
        if not line:
            return "line 不能为空"
        import json as _json
        from datetime import datetime as _now
        PENDING_CLOSE.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": _now.now().strftime("%Y-%m-%dT%H:%M"), "mode": CURRENT["scene"], "line": line}
        PENDING_CLOSE.write_text(_json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return f"已留待补：{line}（完整模式下次开场会看到）"
    if name == "preflight":
        import subprocess
        cmd = [sys.executable, str(SCRIPTS / "preflight.py")]
        if args.get("mode"):
            cmd += ["--mode", args["mode"]]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        return (r.stdout or r.stderr).strip()
    if name == "search_notes":
        kind = args.get("kind") or "触动"
        if kind not in ("触动", "项目"):
            return "kind 只能是 触动 / 项目"
        q = (args.get("query") or "").strip()
        if not q:
            return "query 不能为空"
        import subprocess
        r = subprocess.run([sys.executable, str(SCRIPTS / "l3.py"), "grep", q,
                            "--kind", kind, "--audit", "--limit", "20"],
                           capture_output=True, text=True, encoding="utf-8")
        out = (r.stdout or "").strip()
        return out or f"（{kind} 里没找到「{q}」）"
    if name == "get_snapshot":
        return desk_snapshot()
    if name == "set_snapshot":
        return write_snapshot(args["content"], args.get("client", "unknown"))
    if name == "write_handoff":
        sys.path.insert(0, str(SCRIPTS))
        import handoff as hf
        p = hf.write_session(args["client"], args["content"], args.get("session"))
        cleared = ""
        if PENDING_CLOSE.exists():
            PENDING_CLOSE.unlink()
            cleared = "（已清掉待补标记）"
        return f"已写入 {p.relative_to(ROOT)}{cleared}"
    if name == "wb_retire":
        # 退役免票，但**必须写理由**；没理由等于抹掉为什么
        _why = str(args.get("why") or args.get("reason") or "").strip()
        if not _why:
            return "拒绝：退役决策条目必须写理由（why）——不写理由的退役＝抹掉为什么"
        import subprocess
        wid = (args.get("id") or "").strip()
        why = (args.get("why") or "").strip()
        if not wid:
            return "id 不能为空（如 D-23）"
        if not why:
            return "why 不能为空——为什么退要写进归档，将来才查得到"
        cmd = [sys.executable, str(SCRIPTS / "workbench.py"), "retire", wid, "--why", why]
        if args.get("by"):
            cmd += ["--by", str(args["by"])]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        out = (r.stdout or r.stderr).strip()
        if r.returncode != 0:
            return f"退休失败：{out}"
        # 退完顺手渲染一次（超限则强制，理由写进 _render_log.jsonl）
        r2 = subprocess.run([sys.executable, str(SCRIPTS / "workbench.py"), "render"],
                            capture_output=True, text=True, encoding="utf-8")
        if r2.returncode == 1:
            r2 = subprocess.run([sys.executable, str(SCRIPTS / "workbench.py"), "render",
                                 "--force", "--why", f"wb_retire {wid} 后自动收尾"],
                                capture_output=True, text=True, encoding="utf-8")
        tail = [l for l in (r2.stdout or "").splitlines() if l.strip()][:3]
        return out + ("｜已重渲染：" + " / ".join(tail) if tail else "")
    if name == "list_t1":
        sys.path.insert(0, str(SCRIPTS))
        import handoff as hf
        return hf.t1_block() or "（没有任何出差记忆）"
    if name == "request_card":
        import time as _t
        cid = (args.get("id") or "").strip()
        why = (args.get("why") or "").strip()
        if not cid:
            return "id 不能为空"
        if not why:
            return "why 不能为空——说明为什么需要这张卡"
        if cid in CARD_STATE["denied"]:
            return f"拒绝：{cid} 本会话已被拒过，不能再申请（防死循环）"
        try:
            cat = _cards()
        except RuntimeError as e:
            return f"拒绝：{e}"
        c = cat.get(cid)
        if not c:
            CARD_STATE["denied"].add(cid)
            return f"拒绝：没有这张卡（{cid}）"
        last = CARD_STATE["last"].get(cid, 0)
        if _t.time() - last < COOLDOWN_S:
            return f"拒绝：{cid} 刚申请过，{COOLDOWN_S // 60} 分钟内不重复申请"
        if CARD_STATE["requests"] >= REQ_MAX:
            _assembly_log({"event": "quota_exceeded", "id": cid, "asks": CARD_STATE["requests"]})
            return (f"拒绝：本会话申请额度已用完（{REQ_MAX} 次）。要加卡请让 maker 追加，"
                    "或先用现有卡把能做的做完。")
        # 装前校验（字段/预算/槽位）
        tone = [x for x in CARD_STATE["loaded"] if cat.get(x, {}).get("slot") == "tone"]
        if c.get("slot") == "tone" and tone:
            CARD_STATE["denied"].add(cid)
            return f"拒绝：tone 槽已被「{tone[0]}」占用，同一会话只能有一张改语气的卡"
        CARD_STATE["requests"] += 1
        CARD_STATE["changes"] += 1
        CARD_STATE["last"][cid] = _t.time()
        CARD_STATE["loaded"].append(cid)
        _assembly_log({"event": "request_card", "id": cid, "why": why,
                       "asks": CARD_STATE["requests"], "changes": CARD_STATE["changes"]})
        body = c["_body"].strip()
        return (f"[已装入 {cid}@{c['version']}｜本会话申请 {CARD_STATE['requests']}/{REQ_MAX}]\n\n{body}")
    if name == "unload_card":
        cid = (args.get("id") or "").strip()
        if cid not in CARD_STATE["loaded"]:
            return f"没装这张卡：{cid}"
        if CARD_STATE["changes"] >= CHG_MAX:
            return f"拒绝：本会话装卸变更次数已用完（{CHG_MAX} 次）"
        CARD_STATE["loaded"].remove(cid)
        CARD_STATE["changes"] += 1
        _assembly_log({"event": "unload_card", "id": cid, "changes": CARD_STATE["changes"]})
        return f"[已卸下 {cid}｜变更 {CARD_STATE['changes']}/{CHG_MAX}｜在场：{CARD_STATE['loaded'] or '无'}]"
    if name == "l1_candidate":
        import subprocess
        cmd = [sys.executable, str(SCRIPTS / "l1.py"), "propose",
               "--rule", (args.get("rule") or "").strip(),
               "--refs", (args.get("refs") or "").strip(),
               "--why", (args.get("why") or "").strip(),
               "--impact", (args.get("impact") or "").strip(),
               "--reclaim", (args.get("reclaim") or "").strip(),
               "--occasion", args.get("occasion") or "工作",
               "--impact-kind", args.get("impact_kind") or "做法"]
        for k in ("trigger", "observable", "wrong_if_missing"):
            if args.get(k):
                cmd += ["--" + k.replace("_", "-"), str(args[k])]
        if args.get("honest"):
            cmd += ["--honest"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        out = ((r.stdout or "") + (r.stderr or "")).strip() or "（没有输出）"
        if "证据不足" in out or "证据无效" in out:
            out += ("\n\n【该先做什么】这条先别丢：把它写进 L3（收尾 handoff 的一条，或一条触动）。"
                    "等第二次再出现，就有两条独立证据可以提 L1 了。"
                    "记不下第一次，就永远等不到第二次。")
        return out
    if name == "scan_draft":
        return scan_text(args["text"])
    raise KeyError(name)


def read_resource(uri):
    table = {
        "persona://core": PERSONA,
        "persona://work": WORK,
        "persona://tech": TECH,
        "persona://startup": STARTUP,
        "persona://desk": DESK,
    }
    if uri not in table:
        raise KeyError(uri)
    return read(table[uri])


# ---------------- JSON-RPC ----------------

def handle(req):
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return {"protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": SERVER_INFO}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        try:
            text = call_tool(params.get("name"), params.get("arguments"))
            return {"content": [{"type": "text", "text": text}]}
        except KeyError:
            return {"content": [{"type": "text", "text": f"未知工具：{params.get('name')}"}],
                    "isError": True}
    if method == "resources/list":
        return {"resources": RESOURCES}
    if method == "resources/read":
        try:
            return {"contents": [{"uri": params.get("uri"),
                                  "mimeType": "text/markdown",
                                  "text": read_resource(params.get("uri"))}]}
        except KeyError:
            raise ValueError(f"未知资源：{params.get('uri')}")
    if method == "prompts/list":
        return {"prompts": []}
    raise ValueError(f"未知方法：{method}")


def main():
    # Windows 管道默认用 cp936，必须强制 UTF-8，否则中文 JSON 会乱码
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            result = handle(req)
        except Exception as e:  # noqa: BLE001
            if req.get("id") is not None:
                print(json.dumps({"jsonrpc": "2.0", "id": req["id"],
                                  "error": {"code": -32603, "message": str(e)}},
                                 ensure_ascii=False), flush=True)
            continue
        if result is not None and req.get("id") is not None:
            print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result},
                             ensure_ascii=False), flush=True)


def cli():
    """非 MCP 客户端用：直接打印注入包，复制粘贴即可。"""
    for stream in (sys.stdout,):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    scene, desk = "日常", "full"
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a in ("--print", "-p") and i + 1 < len(argv):
            scene = argv[i + 1]
        elif a == "--desk" and i + 1 < len(argv):
            desk = argv[i + 1]
        elif a in ("--snapshot", "-s"):
            print(desk_snapshot())
            return
    print(build_persona(scene, desk))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli()
    else:
        main()
