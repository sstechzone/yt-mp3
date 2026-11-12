FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1 \\
    DENO_INSTALL=/usr/local

RUN apt-get update && apt-get install -y --no-install-recommends \\
    ffmpeg ca-certificates curl unzip \\
    && rm -rf /var/lib/apt/lists/*

# Deno helps yt-dlp solve YouTube JavaScript challenges.
RUN curl -fsSL https://deno.land/install.sh | sh \\
    && deno --version

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
