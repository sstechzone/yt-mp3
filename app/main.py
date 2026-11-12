from __future__ import annotations

import hmac
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import yt_dlp
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("ytmp3-api")

APP_NAME = "ytmp3-api"
API_KEY = os.getenv("API_KEY", "change-me")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
SOCKET_TIMEOUT = int(os.getenv("SOCKET_TIMEOUT", "20"))
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", tempfile.gettempdir())) / APP_NAME
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
_DOWNLOAD_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_DOWNLOADS)

app = FastAPI(
    title="YouTube to MP3 API",
    version="1.0.0",
    description="Converts supported YouTube video URLs to MP3 audio.",
)


def _validate_url(raw_url: str) -> str:
    """Accept only HTTPS URLs on the explicit YouTube host allowlist."""
    if len(raw_url) > 2048:
        raise HTTPException(status_code=400, detail="URL is too long.")

    parsed = urlparse(raw_url.strip())
    host = (parsed.hostname or "").lower()

    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="Only HTTPS URLs are accepted.")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise HTTPException(status_code=400, detail="Invalid URL authority.")
    if host not in ALLOWED_HOSTS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported URL host. Only YouTube URLs are accepted.",
        )
    if not parsed.path or parsed.path == "/":
        raise HTTPException(status_code=400, detail="URL does not identify a video.")

    return raw_url.strip()


def _check_api_key(provided_key: str | None) -> None:
    if not API_KEY or API_KEY == "change-me":
        logger.error("API_KEY is not configured; refusing authenticated request.")
        raise HTTPException(
            status_code=503,
            detail="Server API key is not configured. Set API_KEY in the environment.",
        )
    if not provided_key or not hmac.compare_digest(provided_key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def _remove_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.exception("Could not remove temporary file: %s", path)


@app.get("/", tags=["status"])
def root():
    return {"service": APP_NAME, "status": "ok", "docs": "/docs"}


@app.get("/health", tags=["status"])
def health():
    return {"status": "healthy"}


@app.get("/download", tags=["download"])
def download_mp3(
    url: str = Query(..., min_length=12, max_length=2048),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _check_api_key(x_api_key)
    video_url = _validate_url(url)

    if not _DOWNLOAD_SEMAPHORE.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="Server is busy. Please retry in a little while.",
            headers={"Retry-After": "15"},
        )

    work_dir = Path(tempfile.mkdtemp(prefix="job-", dir=DOWNLOAD_DIR))
    output_template = str(work_dir / "%(id)s.%(ext)s")

    ydl_options = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": 2,
        "fragment_retries": 2,
        "max_filesize": MAX_FILE_SIZE_MB * 1024 * 1024,
        "restrictfilenames": True,
        "overwrites": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_options) as ydl:
            info = ydl.extract_info(video_url, download=True)
            if not info:
                raise RuntimeError("The video information could not be retrieved.")

        # Find the converted MP3; use the actual downloaded ID rather than trusting a title.
        mp3_files = list(work_dir.glob("*.mp3"))
        if not mp3_files:
            raise RuntimeError("MP3 conversion did not produce an output file.")

        mp3_path = mp3_files[0]
        size = mp3_path.stat().st_size
        if size <= 0:
            raise RuntimeError("The generated MP3 file is empty.")
        if size > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail=f"Generated file exceeds {MAX_FILE_SIZE_MB} MB limit.",
            )

        title = str(info.get("title") or "audio")
        safe_title = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("._-")[:80]
        if not safe_title:
            safe_title = "audio"
        filename = f"{safe_title}.mp3"

        def cleanup_job():
            try:
                for item in work_dir.iterdir():
                    item.unlink(missing_ok=True)
                work_dir.rmdir()
            except OSError:
                logger.exception("Could not clean up job directory: %s", work_dir)
            finally:
                _DOWNLOAD_SEMAPHORE.release()

        return FileResponse(
            path=mp3_path,
            media_type="audio/mpeg",
            filename=filename,
            background=BackgroundTask(cleanup_job),
        )

    except HTTPException:
        for item in work_dir.iterdir():
            item.unlink(missing_ok=True)
        work_dir.rmdir()
        _DOWNLOAD_SEMAPHORE.release()
        raise
    except yt_dlp.utils.DownloadError as exc:
        logger.warning("yt-dlp download failed: %s", exc)
        for item in work_dir.iterdir():
            item.unlink(missing_ok=True)
        work_dir.rmdir()
        _DOWNLOAD_SEMAPHORE.release()
        raise HTTPException(
            status_code=422,
            detail="Could not download this video. It may be unavailable or unsupported.",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected download/conversion failure")
        for item in work_dir.iterdir():
            item.unlink(missing_ok=True)
        work_dir.rmdir()
        _DOWNLOAD_SEMAPHORE.release()
        raise HTTPException(
            status_code=500,
            detail="Download or MP3 conversion failed. Check server logs.",
        ) from exc
