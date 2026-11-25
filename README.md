# Man Bun Rip

A macOS desktop app for bass practice: search YouTube and download tracks as MP3, WAV, M4A or video, optionally slowed down without changing the pitch. Everything happens in the app window. You never need the Terminal once the app is built.

![Man Bun Rip](static/img/bassist.jpg)

## What it does

| Feature | How |
|---|---|
| **Search or paste a link** | One search bar takes a song name (top 10 results) or any YouTube URL (`https://…`, `www.…`, `youtu.be/…`). |
| **Formats** | **MP3 320 kbps** (default), **WAV** (uncompressed, best for DAWs/transcription), **M4A**, **MP4 video up to 1080p**. |
| **Practice speed** | 100% / 90% / 80% / 75% / 60%. Slowing uses ffmpeg's `atempo` filter, which keeps the pitch. MP4 video is slowed to match. |
| **Live progress** | Percent, speed and ETA, streamed via Server-Sent Events. Downloads can be cancelled. |
| **Choose where to save** | Defaults to `~/Downloads`. **Change…** in the sidebar opens a folder picker; the choice is remembered. Files are named like `VULFPECK - Dean Town (80% speed).mp3`, with a **Show in Finder** button when done. |
| **Light & dark mode** | Light by default; the moon/sun button in the top-right switches theme (remembered). |
| **Update yt-dlp** | Header button. YouTube changes often; if downloads start failing, click this and restart the app. |

## Project layout

```
main.py                 pywebview window + FastAPI server on a free localhost port
backend/
  downloader.py         yt-dlp search / downloads / ffmpeg atempo / updater
  server.py             FastAPI routes (JSON + SSE) and static file serving
  paths.py              resource, bin/ and save-folder locations + settings (source + .app aware)
static/                 index.html, styles.css, app.js, img/ (bassist.jpg + outlined cutout PNG)
bin/                    ffmpeg, ffprobe, deno (filled by setup_bins.sh, not committed)
setup_bins.sh           downloads static macOS binaries into bin/
build_mac.sh            builds dist/Man Bun Rip.app with PyInstaller
```

## Build the app (one time, on a Mac)

Requirements: macOS 12+, **Python 3.11+** (from [python.org](https://www.python.org/downloads/macos/) or `brew install python@3.12`), and the Xcode command-line tools (`xcode-select --install`).

```bash
cd man-bun-rip
chmod +x setup_bins.sh build_mac.sh
./build_mac.sh
```

`build_mac.sh` will:

1. create `.venv` and install `requirements.txt` + PyInstaller,
2. run `setup_bins.sh` to download static **ffmpeg**, **ffprobe** and **deno** into `bin/` (if missing),
3. generate the app icon from the bassist illustration,
4. build `dist/Man Bun Rip.app` (`--windowed --onedir`) with `static/` and `bin/` inside it,
5. clear quarantine attributes and ad-hoc sign the bundle.

Then install it:

```bash
cp -R "dist/Man Bun Rip.app" /Applications/
```

### Intel Macs

The script detects your architecture. Static **Intel** ffmpeg builds are getting hard to find, so if the default download fails, point the script at any static x86_64 build (a zip URL or a local file):

```bash
FFMPEG_SRC=/path/to/ffmpeg FFPROBE_SRC=/path/to/ffprobe ./setup_bins.sh --force
```

Homebrew's ffmpeg is **not** static. It works on the Mac that built it but not on other Macs, and the script warns you if a binary links against Homebrew libraries.

## Gatekeeper and code signing

The build is **ad-hoc signed**, not notarized. That's fine on the Mac that built it. When you copy the app to *another* Mac (AirDrop, USB, zip), macOS adds a quarantine flag and may say the app *"is damaged and can't be opened"* or *"cannot be verified"*. Fix it once on that Mac:

```bash
xattr -cr "/Applications/Man Bun Rip.app"
```

Or right-click the app → **Open** → **Open**. On macOS 15+ you may instead need **System Settings → Privacy & Security → Open Anyway**.

If you edit anything inside the bundle afterwards, re-sign it:

```bash
codesign --force --deep --sign - "/Applications/Man Bun Rip.app"
```

To distribute without any warnings, sign with a Developer ID certificate and notarize:

```bash
codesign --force --deep --options runtime --sign "Developer ID Application: YOUR NAME (TEAMID)" "dist/Man Bun Rip.app"
ditto -c -k --keepParent "dist/Man Bun Rip.app" ManBunRip.zip
xcrun notarytool submit ManBunRip.zip --apple-id you@example.com --team-id TEAMID --wait
xcrun stapler staple "dist/Man Bun Rip.app"
```

On first download, macOS asks whether **Man Bun Rip** may access your **Downloads** folder (or whichever folder you picked). Click **Allow**.

## Keeping yt-dlp up to date

Click **Update yt-dlp** in the top-right corner, then quit and reopen the app.

- **Running from source:** runs `python -m pip install -U "yt-dlp[default]"` in the app's own environment.
- **Running the built .app:** a frozen app can't pip-install into itself, so the update is installed into `~/Library/Application Support/Man Bun Rip/pylib` using a system **Python 3.10+**, and the app loads it on the next launch in place of the bundled copy. If no suitable Python is installed, the button says so. Rebuilding with `./build_mac.sh` also picks up the latest yt-dlp.

## Run from source (development)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./setup_bins.sh            # or have ffmpeg/ffprobe on your PATH
python main.py             # native window
python main.py --server    # backend only; open the printed URL in a browser
                           # (add ?theme=dark or ?q=search+terms to the URL)
```

`deno` is optional but recommended: recent YouTube changes require a JavaScript runtime for yt-dlp to see all formats. During development, an installed `node` is used if deno isn't present.

## API (local only, bound to 127.0.0.1)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | yt-dlp version, ffmpeg/deno paths, current save folder |
| GET | `/api/search?q=` | text search (top 10) or URL lookup |
| POST | `/api/download` | `{video_id, format: mp3 (default)\|wav\|m4a\|mp4, speed: 1\|0.9\|0.8\|0.75\|0.6, title}` → `{task_id}` |
| GET | `/api/progress/{task_id}` | Server-Sent Events progress stream |
| POST | `/api/cancel/{task_id}` | cancel a download |
| GET/POST | `/api/download-dir` | read / set the save folder (`{path}`; `null` resets to ~/Downloads) |
| POST | `/api/download-dir/choose` | open the native folder picker |
| POST | `/api/reveal` | `{path}` → `open -R` (only files saved by the app) |
| POST | `/api/update` | upgrade yt-dlp |

## Troubleshooting

- **"Sign in to confirm you're not a bot" / downloads failing:** click **Update yt-dlp** and restart. Make sure `bin/deno` exists.
- **"Save folder is no longer available":** the folder was moved or an external drive unplugged. Click **Change…** to pick another.
- **"ffmpeg was not found":** run `./setup_bins.sh`, then rebuild.

Download only content you have the rights to use, and follow YouTube's Terms of Service.
