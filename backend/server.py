"""FastAPI app: JSON API + Server-Sent Events + static frontend."""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import downloader as dl
from . import paths

app = FastAPI(title=paths.APP_NAME, docs_url=None, redoc_url=None)


class DownloadRequest(BaseModel):
    video_id: str
    format: str = "mp3"
    speed: float = 1.0
    title: str = ""


class RevealRequest(BaseModel):
    path: str


class FolderRequest(BaseModel):
    path: str | None = None  # None resets to ~/Downloads


# The pywebview window (set by main.py) so the API can open native dialogs.
_window = None


def set_window(window) -> None:
    global _window
    _window = window


def _folder_info() -> dict:
    folder = paths.downloads_dir()
    return {
        "download_dir": str(folder),
        "download_dir_display": paths.display_path(folder),
        "is_default": folder == paths.default_downloads_dir(),
        "can_pick_folder": _window is not None,
    }


def _fail(exc: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=dl.clean_error(exc))


@app.get("/api/health")
def health() -> dict:
    return {
        "app": paths.APP_NAME,
        "ytdlp_version": dl.ytdlp_version(),
        "ffmpeg": dl.ffmpeg_path(),
        "ffprobe": dl.ffprobe_path(),
        "deno": dl.deno_path(),
        **_folder_info(),
        "formats": {k: v["label"] for k, v in dl.FORMATS.items()},
        "speeds": list(dl.SPEEDS),
    }


@app.get("/api/search")
async def search(q: str = Query(..., min_length=1, max_length=500)) -> dict:
    try:
        results = await run_in_threadpool(dl.search, q)
    except dl.UserFacingError as exc:
        raise _fail(exc, 422) from exc
    except Exception as exc:
        raise _fail(exc, 502) from exc
    return {"query": q, "results": results}


@app.post("/api/download")
def start_download(req: DownloadRequest) -> dict:
    try:
        task = dl.manager.start(req.video_id, req.format, req.speed, req.title)
    except dl.UserFacingError as exc:
        raise _fail(exc, 422) from exc
    return {"task_id": task.id}


@app.get("/api/progress/{task_id}")
async def progress(task_id: str) -> StreamingResponse:
    task = dl.manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown task.")

    async def events():
        last_rev = -1
        last_send = 0.0
        while True:
            snap = task.snapshot()
            if snap["rev"] != last_rev:
                last_rev = snap["rev"]
                last_send = time.monotonic()
                yield f"data: {json.dumps(snap)}\n\n"
                if task.finished:
                    return
            elif time.monotonic() - last_send > 15:
                last_send = time.monotonic()
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/cancel/{task_id}")
def cancel(task_id: str) -> dict:
    if dl.manager.get(task_id) is None:
        raise HTTPException(status_code=404, detail="Unknown task.")
    return {"cancelled": dl.manager.cancel(task_id)}


@app.post("/api/reveal")
def reveal(req: RevealRequest) -> dict:
    try:
        dl.reveal_in_file_manager(req.path)
    except dl.UserFacingError as exc:
        raise _fail(exc, 422) from exc
    return {"ok": True}


@app.get("/api/download-dir")
def get_download_dir() -> dict:
    return _folder_info()


@app.post("/api/download-dir")
def set_download_dir(req: FolderRequest) -> dict:
    try:
        paths.set_downloads_dir(req.path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _folder_info()


@app.post("/api/download-dir/choose")
async def choose_download_dir() -> dict:
    """Open the native folder picker (desktop window only)."""
    if _window is None:
        raise HTTPException(status_code=409, detail="No native window; send a path instead.")
    import webview

    dialog = webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
    result = await run_in_threadpool(
        _window.create_file_dialog, dialog, directory=str(paths.downloads_dir())
    )
    if not result:
        return {**_folder_info(), "cancelled": True}
    chosen = result[0] if isinstance(result, (list, tuple)) else result
    try:
        paths.set_downloads_dir(chosen)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**_folder_info(), "cancelled": False}


@app.post("/api/update")
async def update() -> dict:
    try:
        return await run_in_threadpool(dl.update_ytdlp)
    except dl.UserFacingError as exc:
        raise _fail(exc, 422) from exc
    except Exception as exc:
        raise _fail(exc, 500) from exc


@app.get("/")
def index() -> FileResponse:
    return FileResponse(paths.static_dir() / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=str(paths.static_dir())), name="static")
