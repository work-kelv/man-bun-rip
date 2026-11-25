"""yt-dlp search, stream extraction, downloads with progress, and ffmpeg practice-speed processing."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

paths.bootstrap()  # must happen before importing yt_dlp

import yt_dlp  # noqa: E402
from yt_dlp.utils import DownloadCancelled, DownloadError  # noqa: E402

# --------------------------------------------------------------------------- constants

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

FORMATS: dict[str, dict[str, Any]] = {
    "wav": {"label": "WAV", "ext": "wav", "audio": True},
    "mp3": {"label": "MP3 320kbps", "ext": "mp3", "audio": True},
    "m4a": {"label": "M4A", "ext": "m4a", "audio": True},
    "mp4": {"label": "MP4 1080p", "ext": "mp4", "audio": False},
}
SPEEDS = (1.0, 0.9, 0.8, 0.75, 0.6)


class UserFacingError(Exception):
    """An error whose message is safe and useful to show in the UI."""


# --------------------------------------------------------------------------- helpers

def ytdlp_version() -> str:
    return yt_dlp.version.__version__


def ffmpeg_path() -> str | None:
    bundled = paths.bundled_binary("ffmpeg")
    return str(bundled) if bundled else shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    bundled = paths.bundled_binary("ffprobe")
    return str(bundled) if bundled else shutil.which("ffprobe")


def deno_path() -> str | None:
    bundled = paths.bundled_binary("deno")
    return str(bundled) if bundled else shutil.which("deno")


def clean_error(exc: BaseException) -> str:
    msg = ANSI_RE.sub("", str(exc)).strip()
    msg = re.sub(r"^ERROR:\s*", "", msg)
    msg = re.sub(r"^\[[^\]]+\]\s*[A-Za-z0-9_-]{11}:\s*", "", msg)
    return msg or exc.__class__.__name__


def format_duration(seconds: Any) -> str:
    if seconds is None:
        return "LIVE"
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "--:--"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def looks_like_url(text: str) -> bool:
    t = text.lower()
    return t.startswith(("http://", "https://", "www.", "youtube.com/", "m.youtube.com/", "youtu.be/", "music.youtube.com/"))


def normalise_url(text: str) -> str:
    return text if text.lower().startswith(("http://", "https://")) else "https://" + text


def safe_filename(name: str, max_len: int = 150) -> str:
    name = re.sub(r"[\\/|]+", "-", name)          # "VULFPECK /// Dean Town" -> "VULFPECK - Dean Town"
    name = name.replace(":", " -").replace('"', "'")
    name = re.sub(r"[*?<>\x00-\x1f]", "", name)
    name = re.sub(r"\s*-[\s-]*-\s*", " - ", name)  # collapse dash runs
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return (name[:max_len].rstrip() or "download")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem} {uuid.uuid4().hex[:6]}{path.suffix}")


class _QuietLogger:
    """Swallow yt-dlp console output (there is no console in the .app); keep the last error."""

    def __init__(self) -> None:
        self.last_error: str | None = None

    def debug(self, msg: str) -> None:  # noqa: D401
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        self.last_error = ANSI_RE.sub("", msg)


def base_opts(**extra: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _QuietLogger(),
        "socket_timeout": 20,
        "retries": 5,
        "fragment_retries": 5,
    }
    bdir = paths.bin_dir()
    if paths.bundled_binary("ffmpeg"):
        opts["ffmpeg_location"] = str(bdir)
    deno = deno_path()
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}
    elif shutil.which("node"):  # dev-machine fallback when deno isn't installed
        opts["js_runtimes"] = {"node": {}}
    opts.update(extra)
    return opts


def _entry_to_result(entry: dict[str, Any]) -> dict[str, Any] | None:
    vid = entry.get("id")
    if not vid or not VIDEO_ID_RE.match(str(vid)):
        return None
    return {
        "id": vid,
        "title": entry.get("title") or "Untitled",
        "uploader": entry.get("uploader") or entry.get("channel") or entry.get("uploader_id") or "",
        "duration": format_duration(entry.get("duration")),
        "duration_seconds": entry.get("duration"),
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
        "url": f"https://www.youtube.com/watch?v={vid}",
    }


# --------------------------------------------------------------------------- search

def search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        raise UserFacingError("Type a song name or paste a YouTube link.")

    if looks_like_url(query):
        opts = base_opts(extract_flat="in_playlist", noplaylist=True, skip_download=True)
        target = normalise_url(query)
    else:
        opts = base_opts(extract_flat=True, skip_download=True)
        target = f"ytsearch{limit}:{query}"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
    except DownloadError as exc:
        raise UserFacingError(clean_error(exc)) from exc

    if not info:
        return []
    entries = info.get("entries")
    raw = list(entries)[:50] if entries is not None else [info]
    results = [r for r in (_entry_to_result(e) for e in raw if e) if r]
    if looks_like_url(query) and not results:
        raise UserFacingError("That link doesn't point to a YouTube video.")
    return results


# --------------------------------------------------------------------------- download tasks

@dataclass
class Task:
    id: str
    video_id: str
    fmt: str
    speed: float
    title: str = ""
    dest_dir: str = ""
    duration: float | None = None
    status: str = "queued"  # queued | downloading | processing | done | error | cancelled
    stage: str = "Waiting…"
    percent: float | None = 0.0
    speed_text: str = ""
    eta_text: str = ""
    filepath: str | None = None
    filename: str | None = None
    error: str | None = None
    rev: int = 0
    cancel_event: threading.Event = field(default_factory=threading.Event)
    proc: subprocess.Popen | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, **changes: Any) -> None:
        with self.lock:
            for key, value in changes.items():
                setattr(self, key, value)
            self.rev += 1

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "task_id": self.id,
                "video_id": self.video_id,
                "format": self.fmt,
                "speed": self.speed,
                "title": self.title,
                "status": self.status,
                "stage": self.stage,
                "percent": None if self.percent is None else round(self.percent, 1),
                "speed_text": self.speed_text,
                "eta_text": self.eta_text,
                "filepath": self.filepath,
                "filename": self.filename,
                "error": self.error,
                "rev": self.rev,
            }

    @property
    def finished(self) -> bool:
        return self.status in ("done", "error", "cancelled")


def _human_speed(bps: float | None) -> str:
    if not bps:
        return ""
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1024:
            return f"{bps:.1f} {unit}" if unit != "B/s" else f"{bps:.0f} {unit}"
        bps /= 1024
    return f"{bps:.1f} TB/s"


def _human_eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d} left"


class DownloadManager:
    def __init__(self, max_workers: int = 3) -> None:
        self.tasks: dict[str, Task] = {}
        self.saved_paths: set[str] = set()
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="download")

    def start(self, video_id: str, fmt: str, speed: float, title: str = "") -> Task:
        if not VIDEO_ID_RE.match(video_id or ""):
            raise UserFacingError("Invalid video id.")
        if fmt not in FORMATS:
            raise UserFacingError(f"Unknown format '{fmt}'.")
        speed = float(speed)
        if not any(abs(speed - s) < 1e-6 for s in SPEEDS):
            raise UserFacingError("Unsupported practice speed.")
        if not ffmpeg_path():
            raise UserFacingError("ffmpeg was not found. Run setup_bins.sh (or install ffmpeg) and restart.")

        task = Task(id=uuid.uuid4().hex, video_id=video_id, fmt=fmt, speed=speed, title=title,
                    dest_dir=str(paths.downloads_dir()))
        self.tasks[task.id] = task
        self.pool.submit(self._run, task)
        return task

    def get(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task.finished:
            return False
        task.cancel_event.set()
        proc = task.proc
        if proc and proc.poll() is None:
            proc.terminate()
        return True

    # ---------------------------------------------------------------- worker

    def _run(self, task: Task) -> None:
        workdir = Path(tempfile.mkdtemp(prefix="manbunrip-"))
        try:
            source = self._download(task, workdir)
            if task.cancel_event.is_set():
                raise DownloadCancelled()

            final_src = source
            if task.speed < 0.999:
                final_src = self._change_speed(task, source, workdir)

            ext = FORMATS[task.fmt]["ext"]
            suffix = "" if task.speed >= 0.999 else f" ({int(round(task.speed * 100))}% speed)"
            dest_dir = Path(task.dest_dir)
            if not dest_dir.is_dir():
                raise UserFacingError(f"Save folder is no longer available: {task.dest_dir}")
            dest = unique_path(dest_dir / f"{safe_filename(task.title or task.video_id)}{suffix}.{ext}")
            shutil.move(str(final_src), dest)
            self.saved_paths.add(str(dest.resolve()))
            task.update(status="done", stage=f"Saved to {paths.display_path(dest_dir)}", percent=100.0, speed_text="", eta_text="",
                        filepath=str(dest), filename=dest.name)
        except DownloadCancelled:
            task.update(status="cancelled", stage="Cancelled", percent=None, speed_text="", eta_text="")
        except (DownloadError, UserFacingError) as exc:
            if task.cancel_event.is_set():
                task.update(status="cancelled", stage="Cancelled", percent=None)
            else:
                task.update(status="error", stage="Failed", error=clean_error(exc), speed_text="", eta_text="")
        except Exception as exc:  # pragma: no cover - last-resort guard
            task.update(status="error", stage="Failed", error=f"Unexpected error: {clean_error(exc)}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _ydl_opts(self, task: Task, workdir: Path) -> dict[str, Any]:
        fmt = task.fmt
        opts = base_opts(
            outtmpl=str(workdir / "%(id)s.%(ext)s"),
            noplaylist=True,
            overwrites=True,
            progress_hooks=[lambda d: self._progress_hook(task, d)],
            postprocessor_hooks=[lambda d: self._postprocessor_hook(task, d)],
        )
        if fmt == "mp4":
            opts["format"] = (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
            )
            opts["merge_output_format"] = "mp4"
            # Make sure non-mp4 codecs (e.g. VP9/Opus) end up in a QuickTime-friendly file.
            opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
        else:
            opts["format"] = "bestaudio[ext=m4a]/bestaudio/best" if fmt == "m4a" else "bestaudio/best"
            pp: dict[str, Any] = {"key": "FFmpegExtractAudio", "preferredcodec": fmt}
            if fmt == "mp3":
                pp["preferredquality"] = "320"
            elif fmt == "m4a":
                pp["preferredquality"] = "256"
            opts["postprocessors"] = [pp]
        return opts

    def _download(self, task: Task, workdir: Path) -> Path:
        task.update(status="downloading", stage="Contacting YouTube…", percent=None)
        with yt_dlp.YoutubeDL(self._ydl_opts(task, workdir)) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={task.video_id}", download=True)
        if info and info.get("title"):
            task.update(title=info["title"])
        if info and info.get("duration"):
            task.duration = float(info["duration"])

        want = FORMATS[task.fmt]["ext"]
        candidates = [p for p in workdir.iterdir() if p.is_file() and p.suffix.lower() == f".{want}"]
        if not candidates:
            candidates = [p for p in workdir.iterdir() if p.is_file() and not p.name.endswith((".part", ".ytdl"))]
        if not candidates:
            raise UserFacingError("Download finished but no output file was produced.")
        return max(candidates, key=lambda p: p.stat().st_size)

    def _progress_hook(self, task: Task, d: dict[str, Any]) -> None:
        if task.cancel_event.is_set():
            raise DownloadCancelled()
        status = d.get("status")
        info = d.get("info_dict") or {}
        if task.fmt == "mp4":
            part = "video" if info.get("vcodec") not in (None, "none") else "audio"
            label = f"Downloading {part}"
        else:
            label = "Downloading audio"

        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            if total:
                pct = done / total * 100
            elif d.get("fragment_count"):
                pct = (d.get("fragment_index") or 0) / d["fragment_count"] * 100
            else:
                pct = None
            task.update(status="downloading", stage=label, percent=pct,
                        speed_text=_human_speed(d.get("speed")), eta_text=_human_eta(d.get("eta")))
        elif status == "finished":
            task.update(stage=f"{label} — done", percent=100.0, speed_text="", eta_text="")

    def _postprocessor_hook(self, task: Task, d: dict[str, Any]) -> None:
        if d.get("status") != "started":
            return
        name = d.get("postprocessor", "")
        labels = {
            "ExtractAudio": f"Converting to {FORMATS[task.fmt]['label']}",
            "Merger": "Merging video + audio",
            "VideoRemuxer": "Finalising MP4",
        }
        if name in labels:
            task.update(status="processing", stage=labels[name] + "…", percent=None, speed_text="", eta_text="")

    # ---------------------------------------------------------------- ffmpeg speed change

    def _probe_duration(self, path: Path) -> float | None:
        probe = ffprobe_path()
        if not probe:
            return None
        try:
            out = subprocess.run(
                [probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=30, **_no_window(),
            )
            return float(out.stdout.strip())
        except (ValueError, subprocess.SubprocessError, OSError):
            return None

    def _change_speed(self, task: Task, src: Path, workdir: Path) -> Path:
        ffmpeg = ffmpeg_path()
        if not ffmpeg:
            raise UserFacingError("ffmpeg not found — cannot change practice speed.")
        pct = int(round(task.speed * 100))
        task.update(status="processing", stage=f"Slowing to {pct}% (pitch preserved)…", percent=0.0,
                    speed_text="", eta_text="")

        duration = task.duration or self._probe_duration(src)
        out_duration = duration / task.speed if duration else None
        dst = workdir / f"slowed-{src.stem}{src.suffix}"
        s = f"{task.speed:g}"

        cmd = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(src)]
        if task.fmt == "mp4":
            cmd += [
                "-filter_complex", f"[0:v]setpts=PTS/{s}[v];[0:a]atempo={s}[a]",
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            ]
        else:
            codec = {
                "wav": ["-c:a", "pcm_s16le"],
                "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
                "m4a": ["-c:a", "aac", "-b:a", "256k"],
            }[task.fmt]
            cmd += ["-vn", "-filter:a", f"atempo={s}", "-map_metadata", "0", *codec]
        cmd += ["-progress", "pipe:1", "-nostats", str(dst)]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **_no_window())
        task.proc = proc
        started = time.time()
        assert proc.stdout is not None
        for line in proc.stdout:
            if task.cancel_event.is_set():
                proc.terminate()
                break
            key, _, value = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and out_duration and value.isdigit():
                done = int(value) / 1_000_000
                frac = max(0.0, min(done / out_duration, 1.0))
                elapsed = time.time() - started
                eta = elapsed / frac - elapsed if frac > 0.02 else None
                task.update(percent=frac * 100, eta_text=_human_eta(eta))
        stderr = proc.stderr.read() if proc.stderr else ""
        code = proc.wait()
        task.proc = None
        if task.cancel_event.is_set():
            raise DownloadCancelled()
        if code != 0 or not dst.exists():
            raise UserFacingError(f"ffmpeg failed while changing speed: {stderr.strip()[-300:] or code}")
        return dst


def _no_window() -> dict[str, Any]:
    """Hide console windows for child processes on Windows (no-op elsewhere)."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


# --------------------------------------------------------------------------- Finder / updates

def reveal_in_file_manager(filepath: str) -> None:
    path = Path(filepath).resolve()
    # Only reveal files this app saved (or anything inside the current save folder).
    if str(path) not in manager.saved_paths and paths.downloads_dir().resolve() not in path.parents:
        raise UserFacingError("Can only reveal files saved by Man Bun Rip.")
    if not path.exists():
        raise UserFacingError("That file no longer exists (was it moved or deleted?).")
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif os.name == "nt":
        subprocess.Popen(["explorer", f"/select,{path}"])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


_update_lock = threading.Lock()


def _find_python_for_update() -> str:
    """The interpreter used to pip-install yt-dlp."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    # Inside the .app, sys.executable is the app itself; use a system Python 3.10+.
    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3"):
        exe = shutil.which(name) or (f"/opt/homebrew/bin/{name}" if Path(f"/opt/homebrew/bin/{name}").exists() else None) \
            or (f"/usr/local/bin/{name}" if Path(f"/usr/local/bin/{name}").exists() else None)
        if not exe:
            continue
        try:
            out = subprocess.run([exe, "-c", "import sys;print(sys.version_info >= (3, 10))"],
                                 capture_output=True, text=True, timeout=20, **_no_window())
            if out.stdout.strip() == "True":
                return exe
        except (subprocess.SubprocessError, OSError):
            continue
    raise UserFacingError("Updating needs Python 3.10+ installed (python.org or Homebrew). "
                          "Alternatively, rebuild the app with build_mac.sh.")


def update_ytdlp() -> dict[str, Any]:
    if not _update_lock.acquire(blocking=False):
        raise UserFacingError("An update is already running.")
    try:
        before = ytdlp_version()
        python = _find_python_for_update()
        env = dict(os.environ)
        cmd = [python, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", "yt-dlp[default]"]
        if getattr(sys, "frozen", False):
            target = paths.updated_pylib_dir()
            cmd += ["--target", str(target)]
            env["PYTHONPATH"] = str(target)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env, **_no_window())
        except subprocess.TimeoutExpired as exc:
            raise UserFacingError("pip timed out — check your internet connection.") from exc
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
            raise UserFacingError("pip failed: " + " ".join(tail))

        probe = subprocess.run([python, "-c", "import yt_dlp;print(yt_dlp.version.__version__)"],
                               capture_output=True, text=True, timeout=60, env=env, **_no_window())
        after = probe.stdout.strip() or before
        return {
            "previous_version": before,
            "version": after,
            "updated": after != before,
            "restart_required": after != before,
        }
    finally:
        _update_lock.release()


manager = DownloadManager()
