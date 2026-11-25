#!/usr/bin/env bash
# Builds "dist/Man Bun Rip.app" with PyInstaller (--windowed --onedir).
#
#   ./build_mac.sh
#   PYTHON=/opt/homebrew/bin/python3.12 ./build_mac.sh   # pick a specific Python 3.11+
set -euo pipefail

cd "$(dirname "$0")"
APP_NAME="Man Bun Rip"
BUNDLE_ID="com.manbunrip.app"
PYTHON="${PYTHON:-python3}"

[[ "$(uname -s)" == "Darwin" ]] || { echo "Build on macOS." >&2; exit 1; }

"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "Python 3.11+ required (found: $("$PYTHON" --version 2>&1)). Install from python.org or Homebrew." >&2
  exit 1
}

# 1. Virtualenv + dependencies -------------------------------------------------
if [[ ! -d .venv ]]; then
  echo "→ Creating .venv"
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt pyinstaller

# 2. Bundled binaries ----------------------------------------------------------
if [[ ! -x bin/ffmpeg || ! -x bin/ffprobe || ! -x bin/deno ]]; then
  echo "→ Fetching ffmpeg / ffprobe / deno"
  ./setup_bins.sh
fi

# 3. App icon from the bassist illustration ------------------------------------
mkdir -p build
ICONSET="build/AppIcon.iconset"
ICON="build/AppIcon.icns"
if [[ ! -f "$ICON" ]]; then
  echo "→ Generating app icon"
  rm -rf "$ICONSET"; mkdir -p "$ICONSET"
  sips -s format png static/img/bassist.jpg --out build/icon_1024.png >/dev/null
  for size in 16 32 128 256 512; do
    sips -z $size $size build/icon_1024.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z $double $double build/icon_1024.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$ICON"
fi

# 4. PyInstaller -----------------------------------------------------------------
echo "→ Running PyInstaller"
rm -rf "dist/$APP_NAME.app" "dist/$APP_NAME"
pyinstaller \
  --noconfirm --clean \
  --windowed --onedir \
  --name "$APP_NAME" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --icon "$ICON" \
  --add-data "static:static" \
  --add-binary "bin/ffmpeg:bin" \
  --add-binary "bin/ffprobe:bin" \
  --add-binary "bin/deno:bin" \
  --collect-all yt_dlp \
  --collect-all yt_dlp_ejs \
  --collect-submodules uvicorn \
  --collect-submodules webview \
  --hidden-import backend.server \
  main.py

APP="dist/$APP_NAME.app"
PLIST="$APP/Contents/Info.plist"

# 5. Info.plist tweaks: allow the WebView to talk to the local http:// server ----
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity dict" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity:NSAllowsLocalNetworking bool true" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :NSAppTransportSecurity:NSAllowsLocalNetworking true" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSDownloadsFolderUsageDescription string 'Man Bun Rip saves your tracks to the Downloads folder.'" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$PLIST" 2>/dev/null || true

# 6. Clear quarantine + ad-hoc sign (needed after editing Info.plist) -----------
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
codesign --verify --deep "$APP" && echo "✓ Ad-hoc signature OK"

echo
echo "Built: $APP"
echo "Run it:  open \"$APP\""
echo "Install: cp -R \"$APP\" /Applications/"
