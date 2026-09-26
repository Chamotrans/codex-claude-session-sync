# Codex Claude Session Sync

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

喺 macOS 上將 **Claude Code** 同 **OpenAI Codex** 嘅對話雙向同步，感覺就好似遊戲嘅雲端存檔：每個對話兩邊都有一份，Codex Claude Session Sync 顯示邊一邊最後編輯過，並將較新嘅回合推去另一邊。無論你下一次打開邊個工具，都會係最新狀態。

- 雙向、只追加嘅同步：一邊嘅新回合會轉換格式並追加到另一邊，唔會改寫任何現有內容。
- 雲端存檔式列表：標題、項目、兩邊嘅最後編輯時間、回合數，以及狀態標籤（`已同步`、`Claude → Codex 2`、`Codex → Claude 1`、`衝突`）。
- 兩邊都有新回合時可以揀點解決：合併兩邊、以 Claude 為準、以 Codex 為準。
- 自動同步：定時掃描加上監察兩個工具嘅 session 資料夾。使用中或者啱啱寫入嘅對話永遠唔會被觸碰。
- 一鍵建立副本：只喺 Claude 存在嘅對話可以生成 Codex thread，反之亦然。
- 同步後嘅對話會出現喺兩個 app 嘅側欄（Codex Desktop 同 Claude desktop app）。
- 附帶批量匯入工具，可以將整個 Codex 歷史搬入 Claude Code（`engine/codex2claude.py`）。

> 狀態：個人工具，按現狀發佈。喺 macOS 27、Claude Code 2.1、Codex CLI 0.153 / Codex Desktop 測試過。App 界面會跟隨 macOS 語言：English、繁體中文（廣東話）或簡體中文。

## 安裝

1. 去 [Releases](../../releases) 下載 `Codex-Claude-Session-Sync-<version>.zip` 並解壓。
2. App 係 ad-hoc 簽名、未經 notarize。第一次打開要右鍵 app → **打開** → **打開**（或者 `xattr -d com.apple.quarantine "Codex Claude Session Sync.app"`）。
3. 需要 PATH 上有 `python3`（Homebrew 或 Xcode Command Line Tools）。引擎只用 Python 標準庫。

或者自行編譯：

```bash
brew install xcodegen
git clone https://github.com/Chamotrans/codex-claude-session-sync.git
cd codex-claude-session-sync && ./build.sh
```

`build.sh` 會生成 Xcode project、無簽名編譯、清走 `com.apple.provenance` 屬性、ad-hoc 簽名並啟動 app。`./build.sh --release` 會用 Release 設定編譯並打包 `dist/Codex-Claude-Session-Sync-<version>.zip`（唔會啟動）；`--no-open` 唔啟動。

執行引擎測試（只用標準庫，用臨時 HOME，唔會碰你嘅 session）：

```bash
python3 -m unittest discover -s engine/tests
```

## 第一次使用

1. App 會掃描 `~/.claude/projects` 同 `~/.codex/sessions`，自動配對兩邊已經存在嘅對話（同一 id，或者 Codex 自己「由 Claude 匯入」嘅記錄）。
2. 狀態唔係 **已同步** 嘅行會有 **同步** 掣；工具列嘅 **同步全部** 會處理所有無衝突嘅配對。
3. 開啟 **自動同步** 就會喺背景持續補齊。設定入面可以再開「自動為只有一邊嘅對話建立副本」。
4. 撳一次 **登記側欄**，Claude desktop app 就會列出由 Codex 過嚟嘅對話（Claude app 係啟動時先讀側欄登記檔，所以之後要重開一次）。

## 運作原理

| | Claude Code | Codex |
|---|---|---|
| Session 檔案 | `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`（每次 resume 一個分段檔） |
| App 讀取嘅索引 | Claude desktop：`~/Library/Application Support/Claude/claude-code-sessions/<org>/<account>/local_*.json`；CLI：直接讀檔案 | `~/.codex/state_5.sqlite` 嘅 `threads` 表 |
| 同步單位 | 一個**人手回合** = 一句用戶指令加上直到下一句指令之前嘅所有內容 | 相同 |

- 一個**配對**連結一個 Claude session 同一個 Codex thread。引擎記住上次同步時兩邊嘅人手回合數；一邊多咗回合就係較新，兩邊都多咗就係衝突。
- Codex → Claude：工具調用會變成真正嘅 `tool_use` / `tool_result` 區塊；加密嘅推理內容會略過；超長 session 會加一個 `compact_boundary` 同合成摘要，令 `claude --resume` 保持快速，完整逐字稿另存喺 `~/.claude/codex-transcripts/<id>.md`。
- Claude → Codex：用 Codex 自己匯入 Claude 時嘅扁平化方式（工具調用變成 `[external_agent_tool_call: …]` 文字），寫一個 rollout 檔同 `threads` 表一行，Codex CLI 同 Codex Desktop 都會列出。
- 使用中（Claude session 登記、Codex writer lock）或者 20 秒內剛寫入嘅對話會略過。

狀態儲存喺 `~/Library/Application Support/SessionSync/`（`state.json` = 配對同游標，`cache.json` = 解析快取）。刪除呢個資料夾就可以由頭嚟過，你嘅 session 檔案唔會受影響。

## 引擎命令列

App 做嘅所有嘢都可以用命令列做：

```bash
python3 engine/sessionsync.py scan --pretty             # 兩邊 session、配對同狀態
python3 engine/sessionsync.py bootstrap                  # 初始配對
python3 engine/sessionsync.py sync --pair ID [--prefer claude|codex|both]
python3 engine/sessionsync.py sync-all
python3 engine/sessionsync.py create --from claude --id ID   # 或 --from codex
python3 engine/sessionsync.py link --claude ID --codex ID
python3 engine/sessionsync.py register-all [--all]       # Claude desktop 側欄登記
python3 engine/sessionsync.py retitle                    # Claude 側改用 Codex 生成嘅標題
python3 engine/sessionsync.py open --side codex --id ID  # 印出 resume 指令
python3 engine/codex2claude.py [--dry-run] [--force]     # 將全部 Codex session 批量匯入 Claude Code
```

## 限制

- 格式係由兩個工具目前嘅 session 檔案逆向整理出嚟，任何一邊改格式都可能失效。
- 推理／思考內容唔會跨工具（Codex 加密儲存；Claude 需要簽名）。
- 同步只追加、以回合為單位。一邊修改或刪除歷史唔會鏡像到另一邊；某邊回合數少過預期時引擎會重設基準。
- 超長嘅 Codex session 喺 Claude 側係摘要而唔係完整重播。

## 授權

MIT
