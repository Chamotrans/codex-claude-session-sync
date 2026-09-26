# Codex Claude Session Sync

**English** · [繁體中文](README.zh-HK.md) · [简体中文](README.zh-CN.md)

Keep your coding-agent conversations in sync between **Claude Code** and **OpenAI Codex** on macOS, the way a game syncs its cloud saves. Every conversation can live in both tools; Codex Claude Session Sync shows which side was edited last and pushes the newer turns across, so whichever app you open next already has the latest state.

- Two-way, append-only sync: new turns from one side are converted and appended to the other. Nothing is rewritten.
- Cloud-save style list: title, project, last edit time on each side, turn counts, and a status pill (`In sync`, `Claude → Codex 2`, `Codex → Claude 1`, `Conflict`).
- Conflict resolution when both sides moved on: merge both, keep Claude, or keep Codex.
- Auto-sync: timer plus file-system watching of both tools' session folders. Open or just-written sessions are never touched.
- One-click counterparts: a conversation that exists only in Claude gets a Codex thread, and vice versa.
- Makes synced sessions visible in both apps' sidebars (Codex Desktop and the Claude desktop app).
- Bulk importer for your whole Codex history into Claude Code (`engine/codex2claude.py`).

> Status: personal tool, released as-is. Tested on macOS 27 with Claude Code 2.1 and Codex CLI 0.153 / Codex Desktop. The app UI follows your macOS language: English, Traditional Chinese (Cantonese) or Simplified Chinese.

## Install

1. Download `Codex-Claude-Session-Sync-<version>.zip` from [Releases](../../releases) and unzip.
2. The app is ad-hoc signed, not notarized. On first launch, right-click the app → **Open** → **Open** (or `xattr -d com.apple.quarantine "Codex Claude Session Sync.app"`).
3. Requires `python3` on your PATH (Homebrew or Xcode Command Line Tools). The engine uses only the Python standard library.

Or build from source:

```bash
brew install xcodegen
git clone https://github.com/Chamotrans/codex-claude-session-sync.git
cd codex-claude-session-sync && ./build.sh
```

`build.sh` generates the Xcode project, builds unsigned, strips the `com.apple.provenance` attributes, ad-hoc signs the app and launches it. `./build.sh --release` builds the Release configuration and packages `dist/Codex-Claude-Session-Sync-<version>.zip` without launching; `--no-open` skips the launch.

Run the engine tests (standard library only, they use a throw-away HOME and never touch your sessions):

```bash
python3 -m unittest discover -s engine/tests
```

## First run

1. The app scans `~/.claude/projects` and `~/.codex/sessions` and pairs conversations that already exist on both sides (same id, or Codex's own "imported from Claude" records).
2. Rows with a status other than **In sync** have a **Sync** button; **Sync all** in the toolbar handles every non-conflict pair.
3. Turn on **Auto sync** to keep things converging in the background. Optionally enable **auto-create counterparts** in Settings.
4. Press **Register sidebar** once so the Claude desktop app lists the sessions that came from Codex (the Claude app reads its sidebar registry at launch, so restart it afterwards).

## How it works

| | Claude Code | Codex |
|---|---|---|
| Session files | `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (one file per resume/segment) |
| Index the app reads | Claude desktop: `~/Library/Application Support/Claude/claude-code-sessions/<org>/<account>/local_*.json`; CLI: the files themselves | `~/.codex/state_5.sqlite` table `threads` |
| Unit of sync | one **human turn** = a user prompt plus everything until the next prompt | same |

- A **pair** links one Claude session with one Codex thread. The engine remembers the human-turn count on each side at the last sync. Extra turns on one side mean that side is newer; extra turns on both sides mean a conflict.
- Codex → Claude: tool calls become real `tool_use` / `tool_result` blocks; encrypted reasoning is skipped; huge sessions get a `compact_boundary` plus a synthetic summary so `claude --resume` stays fast, and the full transcript is exported to `~/.claude/codex-transcripts/<id>.md`.
- Claude → Codex: the same flattening Codex uses for its own Claude import (tool calls become `[external_agent_tool_call: …]` text), a rollout file, and a row in `threads` so both Codex CLI and Codex Desktop list it.
- Sessions that are currently open (Claude session registry, Codex writer locks) or were written in the last 20 s are skipped.

State lives in `~/Library/Application Support/SessionSync/` (`state.json` = pairs and cursors, `cache.json` = parse cache). Delete it to start over; your session files are untouched.

## Engine CLI

Everything the app does is available from the command line:

```bash
python3 engine/sessionsync.py scan --pretty             # both sides, pairs and statuses
python3 engine/sessionsync.py bootstrap                  # seed pairs
python3 engine/sessionsync.py sync --pair ID [--prefer claude|codex|both]
python3 engine/sessionsync.py sync-all
python3 engine/sessionsync.py create --from claude --id ID   # or --from codex
python3 engine/sessionsync.py link --claude ID --codex ID
python3 engine/sessionsync.py register-all [--all]       # Claude desktop sidebar entries
python3 engine/sessionsync.py retitle                    # use Codex's generated titles on the Claude side
python3 engine/sessionsync.py open --side codex --id ID  # prints the resume command
python3 engine/codex2claude.py [--dry-run] [--force]     # bulk import of all Codex sessions into Claude Code
```

## Limitations

- Format knowledge is reverse-engineered from the current session files of both tools and may break when either tool changes its format.
- Reasoning/thinking content does not cross over (Codex stores it encrypted; Claude's needs a signature).
- Sync is append-only and turn-based. Editing or deleting history on one side is not mirrored; the engine re-bases when a side has fewer turns than expected.
- Very long Codex sessions are summarized on the Claude side rather than replayed in full.

## License

MIT
