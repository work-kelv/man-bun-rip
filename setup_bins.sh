#!/usr/bin/env bash
# Downloads static macOS ffmpeg, ffprobe and deno into ./bin so Man Bun Rip is self-contained.
#
#   ./setup_bins.sh            # auto-detect arch (arm64 / x86_64)
#   ./setup_bins.sh --force    # re-download even if binaries exist
#
# Override any source with env vars (zip URL or a local path to an existing static binary):
#   FFMPEG_SRC=...  FFPROBE_SRC=...  DENO_SRC=...  ./setup_bins.sh
set -euo pipefail

cd "$(dirname "$0")"
BIN_DIR="$PWD/bin"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script fetches macOS binaries; run it on a Mac." >&2
  exit 1
fi

ARCH="$(uname -m)"   # arm64 | x86_64
case "$ARCH" in
  arm64)
    DEFAULT_FFMPEG="https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip"
    DEFAULT_FFPROBE="https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffprobe.zip"
    DENO_TRIPLE="aarch64-apple-darwin" ;;
  x86_64)
    # Static Intel builds are becoming scarce; this mirror may 404. If so, pass FFMPEG_SRC/FFPROBE_SRC.
    DEFAULT_FFMPEG="https://ffmpeg.martin-riedl.de/redirect/latest/macos/amd64/release/ffmpeg.zip"
    DEFAULT_FFPROBE="https://ffmpeg.martin-riedl.de/redirect/latest/macos/amd64/release/ffprobe.zip"
    DENO_TRIPLE="x86_64-apple-darwin" ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

FFMPEG_SRC="${FFMPEG_SRC:-$DEFAULT_FFMPEG}"
FFPROBE_SRC="${FFPROBE_SRC:-$DEFAULT_FFPROBE}"
DENO_SRC="${DENO_SRC:-https://github.com/denoland/deno/releases/latest/download/deno-${DENO_TRIPLE}.zip}"

mkdir -p "$BIN_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fetch() {  # fetch <name> <source>
  local name="$1" src="$2" dest="$BIN_DIR/$1"
  if [[ -x "$dest" && $FORCE -eq 0 ]]; then
    echo "✓ $name already present ($dest)"; return
  fi
  echo "→ $name: $src"
  if [[ -f "$src" ]]; then
    cp "$src" "$dest"
  elif [[ "$src" == *.zip ]] || [[ "$src" == http* ]]; then
    local work="$TMP/$name"; mkdir -p "$work"
    if ! curl -fL --retry 3 --progress-bar -o "$work/pkg.zip" "$src"; then
      echo "✗ Could not download $name from $src" >&2
      echo "  Set $(echo "$name" | tr '[:lower:]' '[:upper:]')_SRC to another URL or a local static binary and re-run." >&2
      exit 1
    fi
    unzip -q -o "$work/pkg.zip" -d "$work/x"
    local found
    found="$(find "$work/x" -type f -name "$name" | head -n1)"
    [[ -n "$found" ]] || { echo "✗ $name not found inside archive" >&2; exit 1; }
    cp "$found" "$dest"
  else
    echo "✗ Don't know how to fetch $src" >&2; exit 1
  fi
  chmod +x "$dest"
  xattr -c "$dest" 2>/dev/null || true   # strip quarantine so Gatekeeper doesn't block it
}

fetch ffmpeg  "$FFMPEG_SRC"
fetch ffprobe "$FFPROBE_SRC"
fetch deno    "$DENO_SRC"

echo
echo "Checking binaries…"
for b in ffmpeg ffprobe; do
  "$BIN_DIR/$b" -hide_banner -version | head -n1
  # Warn if the build links against Homebrew/MacPorts libraries (won't work on other Macs).
  if otool -L "$BIN_DIR/$b" | tail -n +2 | grep -Eq '/(opt/homebrew|usr/local|opt/local)/'; then
    echo "  ⚠ $b links to non-system libraries; it is NOT static and may fail on other Macs."
  fi
done
"$BIN_DIR/deno" --version | head -n1
"$BIN_DIR/ffmpeg" -hide_banner -filters 2>/dev/null | grep -q " atempo " && echo "atempo filter: available"
"$BIN_DIR/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -q libmp3lame && echo "libmp3lame encoder: available"

echo
echo "Done. Binaries are in $BIN_DIR"
