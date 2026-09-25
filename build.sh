#!/bin/zsh
# Build, ad-hoc sign and launch Codex Claude Session Sync.
# Files written by sandboxed tools carry com.apple.provenance xattrs, which make Xcode's codesign step
# fail with "resource fork, Finder information, or similar detritus not allowed" — so we strip them and sign ourselves.
set -e
cd "$(dirname "$0")"
pkill -f "Codex Claude Session Sync.app/Contents/MacOS/" 2>/dev/null || true
xattr -cr engine Sources Resources 2>/dev/null || true
rm -rf engine/__pycache__
xcodegen generate >/dev/null
APP="build/Build/Products/Debug/Codex Claude Session Sync.app"
xcodebuild -project SessionSync.xcodeproj -scheme SessionSync -configuration Debug -derivedDataPath build \
  CODE_SIGN_IDENTITY="-" CODE_SIGNING_REQUIRED=NO build 2>&1 | grep -E "error:|BUILD (SUCCEEDED|FAILED)" || true
[ -d "$APP" ] || { echo "build products missing"; exit 1; }
xattr -cr "$APP"
codesign --force --sign - --deep "$APP"
codesign -v "$APP" && echo "signed"
open "$APP"
