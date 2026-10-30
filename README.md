# dsh-her

一个虚拟主播角色的**四层记忆引擎** + 让它在 DSH 里可用的插件。

- `engine/` —— 引擎本体（纯 Python，也能脱离 DSH 单独跑）
- 仓库根 —— DSH 插件（设置页「人格」、`/load` 命令、复制注入包、导入导出角色/技能卡）

## 它解决什么

一个角色要「记得住」，还要「像她」。这两件事不一样：

- **记忆分层**：人格（L0）/ 自我认知（L1）/ 记忆（L2）/ 日志（L3）
- **内容按卡组织**：技能卡（会做什么）/ 任务卡（怎么做）/ 素材卡（这次用什么事实）
- **每次装配都盖章**：装了哪张卡、多少汉字、素材有没有

## 装

### 1. 引擎

```powershell
cd engine
$env:HER_HOME = (Get-Location).Path
python 脚本\her_home.py init --dest .
python 脚本\her-doctor.py      # 应全绿
```

### 2. 插件（DSH）

在 DSH 插件页「添加插件」，填本仓库地址：

```
https://github.com/BReyes010/dsh-her.git
```

装好后在 **设置 → 人格** 里填两行：

- 人格与记忆目录：`<你解压的位置>\engine`
- python 路径：`python.exe` 的完整路径

## 用

| 想干什么 | 怎么做 |
|---|---|
| 新会话带上角色 | 会话里 `/load <角色名> [卡名]`；或设置页「复制注入包」 |
| 换默认角色 | 设置页「设为默认」 |
| 带走一个角色 | 设置页「导出角色（.zip）」→ 另一台机器「导入角色」 |
| 带走一张技能卡 | 「导出技能卡（.zip）」——连它的子卡一起 |
| 坏了 | `python 脚本\her-doctor.py`，每条问题后面有「下一步」 |

## 目录

```
.
├── lib/               插件代码（client.js 界面 / index.js 服务端）
├── tests/             冒烟测试
├── engine/            引擎（脚本 / 入口 / 卡 / 规格 / 模板角色）
│   ├── 脚本/          全部命令行工具（doctor / selftest / grant / card / her_char …）
│   ├── 入口/          her-host（给角色用的工具）/ MCP 服务
│   ├── 人格/          人格卡与 L1 规范（只读，改动走提案）
│   ├── 记忆/          记忆库与日志
│   ├── cards/         角色卡（含空白卡 blank、示例卡 dafeiyu-template）
│   ├── skills/        技能卡 / 任务卡
│   └── 规格/          格式与契约
├── README.md
├── LICENSE
└── 安装说明.md
```

## 外部客户端（可选）

只读 MCP 三个工具：`search_notes` / `recall` / `get_snapshot`（不给写能力）。

- 先双击 `engine\入口\启动-Her-MCP.cmd`，它会打印带 token 的地址；
- `engine\入口\her.mcp.readonly.json` 里是**占位符** `REPLACE_WITH_TOKEN`，必须换成打印出来的串；
- 不改就用，服务端会回 403「token 不对」，不是坏了。

## 边界

- 只承诺本仓库自己的功能；不为第三方 MCP 客户端的协议差异负责
- 角色包默认不带 L3 历史（要带加 `--with-history`）
- 首页（没有会话时）输入框是禁用的，斜杠命令要在会话里用
- 简易审核可以关掉大部分签发，但**改 L0 与恢复任务卡**始终要人工

## 许可

MIT
