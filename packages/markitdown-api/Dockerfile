FROM python:3.13-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive \
    EXIFTOOL_PATH=/usr/bin/exiftool \
    FFMPEG_PATH=/usr/bin/ffmpeg \
    ORT_DISABLE_TELEMETRY=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

WORKDIR /app
COPY packages/markitdown /app/packages/markitdown
COPY packages/markitdown-ocr /app/packages/markitdown-ocr
COPY packages/markitdown-api/pyproject.toml /app/packages/markitdown-api/pyproject.toml
COPY packages/markitdown-api/src /app/packages/markitdown-api/src

RUN python -m pip install --no-cache-dir \
    "/app/packages/markitdown[all]" \
    "/app/packages/markitdown-ocr[llm]" \
    "/app/packages/markitdown-api" \
    && mkdir -p /data/inputs /data/results \
    && chown -R app:app /data

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "markitdown_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
