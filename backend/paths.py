"""Filesystem locations shared by the app (works from source and from a PyInstaller bundle)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "Man Bun Rip"


def resource_root() -> Path:
    """Folder holding bundled read-only resources (static/, bin/)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def static_dir() -> Path:
    return resource_root() / "static"


def bin_dir() -> Path:
    return resource_root() / "bin"


def support_dir() -> Path:
    """Writable per-user folder (holds the self-updated yt-dlp)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def updated_pylib_dir() -> Path:
    """Where 'Update yt-dlp' installs a newer yt-dlp when running as a frozen .app."""
    return support_dir() / "pylib"


def default_downloads_dir() -> Path:
    """The Desktop (Windows may redirect it into OneDrive)."""
    desktop = Path.home() / "Desktop"
    onedrive = Path.home() / "OneDrive" / "Desktop"
    if not desktop.is_dir() and onedrive.is_dir():
        return onedrive
    return desktop


def _settings_file() -> Path:
    return support_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    _settings_file().write_text(json.dumps(settings, indent=2), encoding="utf-8")


def downloads_dir() -> Path:
    """The user's chosen save folder, falling back to the Desktop if it's gone."""
    chosen = load_settings().get("download_dir")
    if chosen and Path(chosen).is_dir():
        return Path(chosen)
    path = default_downloads_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def set_downloads_dir(folder: str | None) -> Path:
    """Persist a new save folder (None resets to the Desktop). Raises ValueError if unusable."""
    settings = load_settings()
    if not folder:
        settings.pop("download_dir", None)
        save_settings(settings)
        return downloads_dir()
    path = Path(folder).expanduser()
    if not path.is_dir():
        raise ValueError("That folder doesn't exist.")
    if not os.access(path, os.W_OK):
        raise ValueError("Man Bun Rip can't write to that folder.")
    settings["download_dir"] = str(path.resolve())
    save_settings(settings)
    return path.resolve()


def display_path(path: Path) -> str:
    """~/Music/Bass instead of /Users/name/Music/Bass."""
    try:
        return "~" + os.sep + str(path.relative_to(Path.home())) if path != Path.home() else "~"
    except ValueError:
        return str(path)


def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def bundled_binary(name: str) -> Path | None:
    candidate = bin_dir() / _exe(name)
    return candidate if candidate.is_file() else None


def bootstrap() -> None:
    """Must run before `import yt_dlp`.

    * Puts ./bin first on PATH so yt-dlp finds the bundled ffmpeg/ffprobe/deno.
    * Puts the self-update folder first on sys.path so an updated yt-dlp wins
      over the copy frozen inside the .app.
    """
    bdir = bin_dir()
    if bdir.is_dir():
        for exe in bdir.iterdir():
            if exe.is_file() and os.name != "nt":
                try:
                    exe.chmod(exe.stat().st_mode | 0o111)
                except OSError:
                    pass
        os.environ["PATH"] = str(bdir) + os.pathsep + os.environ.get("PATH", "")

    if getattr(sys, "frozen", False):
        pylib = updated_pylib_dir()
        if (pylib / "yt_dlp").is_dir() and str(pylib) not in sys.path:
            sys.path.insert(0, str(pylib))
