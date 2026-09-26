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
- **15 Claude-origin sessions carry a wrong "[Codex] " title** (my fault: the first `retitle` run on 2026-09-25 ran
  over every pair before it was limited to Codex-origin pairs). Examples: "Capturela 應用架構規劃",
  "opencodex repository", "Hermes Skill 部署方法". Two also have the wrong title in the Claude desktop sidebar.
  Preview and fix (writes one title line per session + 2 sidebar entries; restart the Claude app afterwards):
  `python3 engine/sessionsync.py fix-titles` (dry run) then `python3 engine/sessionsync.py fix-titles --apply`.
- **Codex auto-import setting.** `~/.codex/config.toml` has `external-agent-import-sync-enabled = true`. Every Claude
  copy this app makes of a Codex thread will be imported back once (one batch of 36 on 2026-09-26 07:35; no file was
  imported twice, so it is one echo per copy, not one per change). Options: leave it (echoes are hidden under
  "Re-imports"), or set it to `false` so this app is the only bridge.
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

### 2026-09-27 — iteration 1: tests + echo prevention
- Added `engine/tests` (stdlib unittest, 19 tests). `FakeHome` builds synthetic Claude/Codex stores in a temp HOME and
  runs the engine as a subprocess. Run: `python3 -m unittest discover -s engine/tests`.
- Tests found two real bugs, both fixed:
  - `sync --prefer both` (conflict "merge") read the Codex delta after appending Claude's turns to Codex, so those
    turns were copied back into Claude as duplicates. Not hit on real data (no merge was ever run).
  - Sidebar registration failed when the Claude desktop account folder had no `local_*.json` yet.
- Echo prevention (backlog 1): Codex re-imports of already-paired Claude sessions are detected from
  `external_agent_session_imports.json`, listed separately (`echoes` in scan, "重複匯入" filter in the app) and
  excluded from "只有 Codex", auto-create and bootstrap. `create` now refuses to copy an echo or a session that
  already has a counterpart (override with `--force`).
- Real data (read-only scan): 37 echoes recognised; unpaired Codex dropped from 38 to 1 genuine thread.

### 2026-09-27 — iteration 2: vanished sessions
- Scan now reports `missing_side` (claude / codex / both) for pairs that lost a side. Archived Codex threads were
  already followed into `archived_sessions` and are not "missing" (test added).
- New `recreate --pair ID`: rebuilds the deleted side from the surviving one and repoints the pair; restores the
  pair untouched if creation fails. App: "重建" button on the row and in the detail view, clearer explanation.
- App decodes unknown pair statuses as "未知" instead of failing the whole scan (forward compatibility).
- Tests: 23 passing. Real data: the 4 "missing" pairs are all `missing_side = codex` (threads deleted in Codex);
  left for the owner to recreate or unlink.

### 2026-09-27 — iteration 3: CI + public-build robustness
- GitHub Actions `CI`: engine tests on Ubuntu (Python 3.12) and an unsigned Release `xcodebuild` on macOS. First
  run green on the branch.
- Engine tests also pass on the macOS system Python 3.9.6. One unexplained 3.9 failure on the very first run;
  9 reruns clean, not reproducible — watching CI for recurrence.
- App finds python3 itself (Homebrew, python.org, then /usr/bin/python3) instead of `/usr/bin/env python3`, shows
  install instructions when python3 is missing or is the Command Line Tools stub, and drains stderr on a separate
  thread (a large traceback could previously deadlock the pipe). Settings shows the Python in use.
- `build.sh`: `--release` (Release config, zip into dist/, no launch) and `--no-open`. Building with
  `CODE_SIGNING_ALLOWED=NO` removes the recurring "detritus" codesign failure; we sign afterwards. Tests are
  excluded from the app bundle. READMEs (3 languages) document tests and release builds.

### 2026-09-27 — iteration 4: UI localization
- All 157 UI strings moved to English source keys with a String Catalog (`Sources/SessionSync/Localizable.xcstrings`):
  zh-Hant keeps the original Cantonese wording, zh-Hans is new (Mandarin written style). Computed labels and
  messages use `String(localized:)`; helper views take `LocalizedStringKey`.
- Keys were taken from the compiler's own extraction (`SWIFT_EMIT_LOC_STRINGS=YES` → `.stringsdata`), so nothing is
  missing; a script checked that `%lld`/`%@` specifiers match in every translation.
- Verified in the built bundle: localizations `en`, `zh-Hant`, `zh-Hans`; formatted strings render correctly in all
  three. The app was launched once with `HOME` pointed at a throw-away fixture home (real `state.json` untouched).
  No screenshot: this process has no screen-recording permission and the new bundle id needs your approval for the
  screenshot tool — **please eyeball the English UI** (`open -n <app> --args -AppleLanguages '(en)'`).
- READMEs updated: UI follows the macOS language.

### 2026-09-27 — iteration 5: app icon
- Icon drawn in code (`tools/make_icon.swift`): Claude-orange → Codex-ink gradient squircle on the macOS icon grid
  with the SF Symbol `arrow.triangle.2.circlepath.icloud.fill`. All 10 mac sizes in
  `Resources/Assets.xcassets/AppIcon.appiconset`; `AppIcon.icns` + `Assets.car` now ship in the bundle.
- Backlog 1–5 done. Next: polish/review passes.

### 2026-09-27 — iteration 6: review follow-ups
- Read-only check of Codex's import records: 86 records, no source imported twice, the 36 echoes arrived in one batch.
  Echoes are a one-off per Claude copy, not a leak per sync. README (3 languages) now explains re-imports and the
  `external-agent-import-sync-enabled` switch.
- Read-only audit found the retitle damage listed at the top. New `fix-titles` (dry run by default, `--apply` to write)
  only touches titles this engine wrote or that Claude copied back verbatim; restores Claude's earlier title, else
  drops the prefix. 5 fixture tests. Dry run on real data lists exactly the 15 sessions. **Not applied.**
- CI: macOS job now also runs the tests under `/usr/bin/python3` (3.9). `FakeHome.pair()` raises a descriptive
  assertion instead of `StopIteration` (likely source of the one-off 3.9 ERROR). 28 tests pass on 3.9 and 3.13.
- READMEs document Re-imports, `recreate` and `fix-titles`.
- Draft PR opened from this branch; not merged. Loop stopped: backlog done, remaining items need the owner.
