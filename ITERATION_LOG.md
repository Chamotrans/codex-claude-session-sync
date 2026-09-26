# Iteration log (unattended, started 2026-09-27)

## Goal
Harden Codex Claude Session Sync while the owner is away: prevent sync loops, handle vanished sessions, add a
fixture-based test suite + CI, make the public build robust, and localize the UI (English + 繁體中文).

## Guardrails
- Work only on branch `iterate/2026-09-27`. No pushes to `main`, no new tags or releases.
- Never run `sync`, `create`, `register`, `retitle` or `bootstrap` against the real `~/.claude` / `~/.codex`.
  Engine checks run with `HOME=<temp dir>` and synthetic fixtures; real data is only read.
- Do not launch the app (it bootstraps on appear). Build with plain `xcodebuild`.

## Waiting for the owner
- **Echo duplicates in Codex (36 threads).** Codex Desktop auto-imported the 111 Codex-derived Claude sessions
  back into Codex (`external_agent_session_imports.json` now has 36 records whose source is a `019…`/`01a…`
  Claude file). They show up as unpaired "[Codex] …" threads. Clean-up (archive them in Codex) needs your OK.
- **4 pairs "missing" a Codex side** (NSFW test ×3, Sandbox mode prompt override): the Codex threads were deleted.
  Decide: unlink, or recreate the Codex copy.
- **Pending real syncs:** 3 pairs Codex-newer, 1 pair Claude-newer. Not touched.
- Merge this branch to `main` and decide whether to cut v1.0.1.

## Backlog (priority order)
1. Echo/loop prevention: detect Codex re-imports of engine-made Claude files, link them to the existing pair or
   hide them; never create counterparts of counterparts; mark engine-created files.
2. Missing/archived handling: archived → follow into `archived_sessions`; deleted → status `orphaned` with unlink
   / recreate actions instead of a stuck `missing`.
3. Tests + CI: fixture HOME, conversion, segment dedupe, delta append both ways, conflict, rebase, echo detection.
   GitHub Actions: pytest + `xcodebuild`.
4. Public-build robustness: clear message when `python3` is missing/stub; Release configuration in build/release.
5. UI localization (String Catalog: en + zh-Hant) and an app icon.

## Log
### 2026-09-27 — iteration 0: diagnosis (read-only)
- Real data: Claude 135, Codex 166 (was 132), pairs 132; 124 in sync, 3 codex_newer, 1 claude_newer, 4 missing;
  unpaired Codex 38 (36 are echo imports, 2 are new real threads), unpaired Claude 3.
- App auto-sync is off (`defaults read com.sunnyyylai.sessionsync autoSync` = 0), so no loop damage beyond the
  one-off Codex import.
