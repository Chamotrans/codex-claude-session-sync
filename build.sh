#!/bin/zsh
# Build, ad-hoc sign and (optionally) launch Codex Claude Session Sync.
#
#   ./build.sh              Debug build, sign, launch
#   ./build.sh --no-open    Debug build, sign, do not launch
#   ./build.sh --release    Release build, sign, zip into dist/ (never launches)
#
# Files written by sandboxed tools carry com.apple.provenance xattrs, which make Xcode's codesign step
# fail with "resource fork, Finder information, or similar detritus not allowed" — so we build unsigned,
# strip them and sign ourselves.
set -e
cd "$(dirname "$0")"

CONFIG=Debug
OPEN=1
for arg in "$@"; do
  case "$arg" in
    --release) CONFIG=Release; OPEN=0 ;;
    --no-open) OPEN=0 ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

APP_NAME="Codex Claude Session Sync"
APP="build/Build/Products/$CONFIG/$APP_NAME.app"
[ "$OPEN" = 1 ] && pkill -f "$APP_NAME.app/Contents/MacOS/" 2>/dev/null || true

xattr -cr engine Sources Resources 2>/dev/null || true
rm -rf engine/__pycache__ engine/tests/__pycache__
xcodegen generate >/dev/null
rm -rf "$APP"
xcodebuild -project SessionSync.xcodeproj -scheme SessionSync -configuration "$CONFIG" -derivedDataPath build \
  CODE_SIGNING_ALLOWED=NO build 2>&1 | grep -E "error:|BUILD (SUCCEEDED|FAILED)" || true
[ -d "$APP" ] || { echo "build products missing"; exit 1; }

# Tests are not part of the shipped engine.
rm -rf "$APP/Contents/Resources/engine/tests"
xattr -cr "$APP"
codesign --force --sign - --deep "$APP"
codesign -v "$APP" && echo "signed: $APP"

if [ "$CONFIG" = Release ]; then
  VERSION=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" "$APP/Contents/Info.plist")
  mkdir -p dist
  ZIP="dist/Codex-Claude-Session-Sync-$VERSION.zip"
  rm -f "$ZIP"
  ditto -c -k --keepParent "$APP" "$ZIP"
  echo "packaged: $ZIP"
fi

[ "$OPEN" = 1 ] && open "$APP" || true
