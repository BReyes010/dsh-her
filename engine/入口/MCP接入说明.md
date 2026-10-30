# 接入说明：把她的工具挂到别的客户端（千问桌面版 / Claude Desktop / …）

写于 2026-10-28 · 用途：让外部客户端也能**按需查**她的记忆（不查就不花 token）。

---

## 一、先分清两件事

| | 能不能做 |
|---|---|
| 让客户端**每轮自动注入**她的检索结果（真 hook） | ❌ 外部客户端不给改上下文的接口 |
| 让**模型自己决定要不要调工具**（MCP） | ✅ **这就是本说明做的事** |

差别：hook 是我们偷看消息；MCP 是她**主动查**。后者不需要改任何请求上下文。

---

## 二、网络前提（最关键）

MCP 地址是 `http://127.0.0.1:8790/mcp?token=…`。

| 客户端的 MCP 跑在哪 | 能不能连上 |
|---|---|
| **客户端本机**（桌面版在你这台机器上起进程/连本地地址） | ✅ 能 |
| 客户端云端（它要求公网 URL） | ❌ 连不到 127.0.0.1，需要内网穿透（**不建议**） |

**先确认这一点再往下做**：在千问那边问「MCP 服务器是本地起还是你们云端连？」——
如果是本地，直接用本说明；如果是云端，这条路暂时走不通。

---

## 三、启动

双击 `入口\启动-Her-MCP.cmd`（或）：

```powershell
python "{PROJECT_ROOT}\{角色名}{项目名}\人格与记忆\入口\her-mcp-http.py" --port 8790 --print-url
```

它会打印：

```
http://127.0.0.1:8790/mcp?token=<32位随机串>
```

token 存在 `入口\mcp-token.txt`（首次自动生成）。**换 token 就删掉这个文件重启**。

自检：

```powershell
Invoke-RestMethod http://127.0.0.1:8790/health
```

---

## 四、两种填法（任选其一，先问客户端支持哪种）

### A. HTTP（客户端连本机地址）

| 项 | 值 |
|---|---|
| 地址 | `http://127.0.0.1:8790/mcp?token=<token>` |
| 或 | 不带 token，改用 Header `Authorization: Bearer <token>` |

启动：双击 `入口\启动-Her-MCP.cmd`（窗口别关）。

### B. stdio（客户端在本机起进程）

| 项 | 值 |
|---|---|
| command | `{DSH_HOME}\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe` |
| args | `{PROJECT_ROOT}\{角色名}{项目名}\人格与记忆\入口\her-mcp-stdio.py` |

**B 版也是只读**（同样三个工具，没有写能力），适合"本地起进程"的客户端。

手动自检：

```powershell
'{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python 入口\her-mcp-stdio.py
```

## 四·五、开机自启（已装）

已装：启动目录下的 `HerMCP.lnk` → 调 `wscript` 跑 `入口\her-mcp-autostart.vbs` → **静默**起服务（无窗口），日志写 `入口\mcp.log`。

| 想干什么 | 怎么做 |
|---|---|
| 确认在跑 | 浏览器开 `http://127.0.0.1:8790/health` |
| 看日志 | `入口\mcp.log`（每次启动打一行 start + URL） |
| 临时停 | 关掉/杀掉 python 进程；或跑 `入口\卸载自启.cmd` |
| 卸载自启 | 双击 `入口\卸载自启.cmd`（删快捷方式 + 停进程） |

> 自启脚本是**纯 ASCII**：`.vbs` / `.cmd` 里写中文会被 GBK 控制台啃掉（踩过）。

### 忘带 token 时

地址在日志里：`入口\mcp.log` 的 `URL:` 行；token 本体在 `入口\mcp-token.txt`。

## 五、在客户端里填什么

| 项 | 值 |
|---|---|
| 类型 | **Streamable HTTP** 或 **HTTP+SSE**（两种都支持） |
| 地址 | `http://127.0.0.1:8790/mcp?token=<token>` |
| SSE 端点 | `http://127.0.0.1:8790/sse?token=<token>`（先发 `endpoint`，再往 `/messages?session=…` POST） |
| 鉴权 | 也可用 Header `Authorization: Bearer <token>`（地址就不带 ?token=） |
| 协议版本 | `2024-11-05`（initialize 会回这个） |

> 如果它只支持 stdio：把命令填成
> `{DSH_HOME}\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe`
> 参数填 `{PROJECT_ROOT}\{角色名}{项目名}\人格与记忆\入口\her-host.py`
> ——那是她的完整工具集（**含写能力**，只建议在 DSH 里用）。

---

## 六、挂上去以后，她能调三个工具

| 工具 | 干什么 | 花多少 |
|---|---|---|
| `search_notes` | 查 `触动.md` / `项目思考.md`（她自己的两份笔记） | 命中才回几条 |
| `recall` | 查 L3 永久记忆（时间 + 关键词 → ≤3 条片段，带出处） | 命中才回 |
| `get_snapshot` | 读任务看板（现在/卡在哪/下一步） | 一次 |

**只有读，没有写**：handoff、笔记、改任务看板一律不暴露——那些只归 DSH 里的完整模式。

每次调用都会写一行审计：`记忆/L3/_查询审计.jsonl`。

---

## 七、验收三句

1. 列表里能看到 3 个工具；
2. 让她「查查我记过的关于完整的那句」→ 应该调 `search_notes` 并回原文；
3. 问「今天天气」→ **不该调工具**（别乱查）。

---

## 八、安全

| 项 | 做法 |
|---|---|
| 只监听回环 | `--host 127.0.0.1`（默认），局域网连不到 |
| token | 32 位随机；地址或 Header 二选一 |
| 只读 | 写工具不注册 |
| 审计 | 谁查了什么，落 `_查询审计.jsonl` |

**不要把带 token 的地址发到公开地方**——拿到地址的人能查她的记忆。

---

## 九、发布边界（2026-10-28 定）

**我们不为「其他用户的 MCP 适配」负责，只做好自己的功能。**

| 我们给 | 我们不负责 |
|---|---|
| 这两个服务（`her-mcp-http.py` / `her-mcp-stdio.py`）能跑、三工具能用、有审计 | 某个第三方 App **一定能**接上 |
| 这份说明（两种传输、token、自启、日志位置） | 追各家客户端的协议版本变化 |
| 自测命令与日志，便于你自己判断哪一步断了 | 远程调试对方的客户端 |

**已知实例**（2026-10-28）：千问桌面 `GET /health` 通，内置 MCP 客户端握手失败。
我们给出的是「服务在这里 + 怎么接 + 怎么看日志」，接不上由客户端负责。

> **配置里的 token 必须换掉**：`her.mcp.readonly.json` 里写的是占位符 `REPLACE_WITH_TOKEN`。
> 直接复制不改，服务端会回 403「token 不对」。
> 正确做法：先双击 `启动-Her-MCP.cmd`，用它打印出来的带 token 的完整地址替换。
