# Codex Claude Session Sync

[English](README.md) · [繁體中文](README.zh-HK.md) · **简体中文**

在 macOS 上把 **Claude Code** 和 **OpenAI Codex** 的对话双向同步，就像游戏的云存档：每个对话在两边各有一份，Codex Claude Session Sync 会显示哪一边最后编辑过，并把较新的回合推送到另一边。无论下次打开哪个工具，都是最新状态。

- 双向、仅追加的同步：一边的新回合会转换格式后追加到另一边，不会改写任何已有内容。
- 云存档式列表：标题、项目、两边的最后编辑时间、回合数，以及状态标签（`已同步`、`Claude → Codex 2`、`Codex → Claude 1`、`冲突`）。
- 两边都有新回合时可选择处理方式：合并两边、以 Claude 为准、以 Codex 为准。
- 自动同步：定时扫描加上监听两个工具的 session 目录。正在使用或刚写入的对话永远不会被触碰。
- 一键创建副本：只存在于 Claude 的对话可以生成 Codex thread，反之亦然。
- 同步后的对话会出现在两个 app 的侧栏中（Codex Desktop 和 Claude 桌面版）。
- 附带批量导入工具，可把整个 Codex 历史迁入 Claude Code（`engine/codex2claude.py`）。

> 状态：个人工具，按现状发布。在 macOS 27、Claude Code 2.1、Codex CLI 0.153 / Codex Desktop 上测试过。App 界面目前为繁体中文（粤语）。

## 安装

1. 到 [Releases](../../releases) 下载 `Codex-Claude-Session-Sync-<version>.zip` 并解压。
2. App 为 ad-hoc 签名、未经公证。首次打开需右键 app → **打开** → **打开**（或执行 `xattr -d com.apple.quarantine "Codex Claude Session Sync.app"`）。
3. 需要 PATH 中有 `python3`（Homebrew 或 Xcode Command Line Tools）。引擎只依赖 Python 标准库。

或从源码构建：

```bash
brew install xcodegen
git clone https://github.com/Chamotrans/codex-claude-session-sync.git
cd codex-claude-session-sync && ./build.sh
```

`build.sh` 会生成 Xcode 工程、编译、清除导致 Xcode 签名步骤失败的 `com.apple.provenance` 属性、ad-hoc 签名并启动 app。

## 首次使用

1. App 会扫描 `~/.claude/projects` 和 `~/.codex/sessions`，自动配对两边已存在的对话（相同 id，或 Codex 自己"从 Claude 导入"的记录）。
2. 状态不是 **已同步** 的行会有 **同步** 按钮；工具栏的 **同步全部** 会处理所有无冲突的配对。
3. 打开 **自动同步** 即可在后台持续补齐。设置中可再开启"自动为只有一边的对话创建副本"。
4. 点一次 **登记侧栏**，Claude 桌面版就会列出来自 Codex 的对话（Claude app 在启动时读取侧栏登记文件，之后需重启一次）。

## 工作原理

| | Claude Code | Codex |
|---|---|---|
| Session 文件 | `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`（每次 resume 一个分段文件） |
| App 读取的索引 | Claude 桌面版：`~/Library/Application Support/Claude/claude-code-sessions/<org>/<account>/local_*.json`；CLI：直接读文件 | `~/.codex/state_5.sqlite` 的 `threads` 表 |
| 同步单位 | 一个**人工回合** = 一条用户指令加上直到下一条指令之前的全部内容 | 相同 |

- 一个**配对**连接一个 Claude session 和一个 Codex thread。引擎记录上次同步时两边的人工回合数；一边多出回合即为较新，两边都多出即为冲突。
- Codex → Claude：工具调用转换为真正的 `tool_use` / `tool_result` 块；加密的推理内容跳过；超长 session 会加一个 `compact_boundary` 和合成摘要，让 `claude --resume` 保持快速，完整文字记录另存于 `~/.claude/codex-transcripts/<id>.md`。
- Claude → Codex：采用 Codex 自身导入 Claude 时的扁平化方式（工具调用变成 `[external_agent_tool_call: …]` 文本），写入一个 rollout 文件和 `threads` 表的一行，Codex CLI 和 Codex Desktop 都会列出。
- 正在使用（Claude session 登记、Codex writer lock）或 20 秒内刚写入的对话会跳过。

状态保存在 `~/Library/Application Support/SessionSync/`（`state.json` = 配对与游标，`cache.json` = 解析缓存）。删除该目录即可重新开始，你的 session 文件不受影响。

## 引擎命令行

App 的全部功能都可通过命令行使用：

```bash
python3 engine/sessionsync.py scan --pretty             # 两边 session、配对与状态
python3 engine/sessionsync.py bootstrap                  # 初始配对
python3 engine/sessionsync.py sync --pair ID [--prefer claude|codex|both]
python3 engine/sessionsync.py sync-all
python3 engine/sessionsync.py create --from claude --id ID   # 或 --from codex
python3 engine/sessionsync.py link --claude ID --codex ID
python3 engine/sessionsync.py register-all [--all]       # Claude 桌面版侧栏登记
python3 engine/sessionsync.py retitle                    # Claude 侧改用 Codex 生成的标题
python3 engine/sessionsync.py open --side codex --id ID  # 打印 resume 命令
python3 engine/codex2claude.py [--dry-run] [--force]     # 将全部 Codex session 批量导入 Claude Code
```

## 限制

- 格式由两个工具当前的 session 文件逆向整理而来，任一方更改格式都可能失效。
- 推理/思考内容不会跨工具（Codex 加密存储；Claude 需要签名）。
- 同步仅追加、以回合为单位。一边修改或删除历史不会镜像到另一边；某边回合数少于预期时引擎会重设基准。
- 超长的 Codex session 在 Claude 侧为摘要而非完整重放。

## 许可

MIT
