import os
import shutil
import tempfile
import secrets
import logging
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask


app = FastAPI(
    title="YT MP3 Downloader API",
    description="Direct MP3 download API",
    version="1.0.0"
)

API_KEY = os.getenv("API_KEY", "")
MAX_FILE_SIZE = 100 * 1024 * 1024

SUPPORTED_HOSTS = [
    host.strip().lower()
    for host in os.getenv(
        "SUPPORTED_HOSTS",
        "youtube.com,youtu.be,soundcloud.com"
    ).split(",")
    if host.strip()
]


def validate_url(url: str):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        if parsed.scheme != "https":
            raise ValueError()

        if not any(
            host == domain or host.endswith("." + domain)
            for domain in SUPPORTED_HOSTS
        ):
            raise ValueError()

        return url

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Unsupported or invalid URL"
        )


def authenticate(api_key: str):
    if not API_KEY:
        raise HTTPException(
            status_code=503,
            detail="API authentication is not configured"
        )

    if not secrets.compare_digest(api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )


@app.get("/")
def home():
    return {
        "status": "online",
        "name": "YT MP3 Downloader API",
        "version": "1.0.0"
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/download")
def download_mp3(
    url: str = Query(...),
    key: str = Header(..., alias="X-API-Key")
):
    authenticate(key)
    validate_url(url)

    temp_dir = tempfile.mkdtemp(prefix="ytmp3_")

    try:
        output_template = str(
            Path(temp_dir) / "%(id)s.%(ext)s"
        )

        options = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 20,
            "retries": 2,
            "fragment_retries": 2,
            "max_filesize": MAX_FILE_SIZE,
            "restrictfilenames": True,
            "overwrites": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])

        mp3_files = list(Path(temp_dir).glob("*.mp3"))

        if not mp3_files:
            raise RuntimeError("MP3 conversion failed")

        mp3_file = mp3_files[0]

        if mp3_file.stat().st_size > MAX_FILE_SIZE:
            raise RuntimeError("File size limit exceeded")

        return FileResponse(
            path=str(mp3_file),
            media_type="audio/mpeg",
            filename="download.mp3",
            background=BackgroundTask(
                shutil.rmtree,
                temp_dir,
                ignore_errors=True
            )
        )

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Replace your existing except Exception block with this:

except Exception as e:
    logger.exception("YT-DLP DOWNLOAD ERROR")

    shutil.rmtree(temp_dir, ignore_errors=True)

    raise HTTPException(
        status_code=400,
        detail=f"Download failed: {str(e)[:500]}"
    )
