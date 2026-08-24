## Aegis Labs — backend (FastAPI) container image.
## Build context is the repo root; uploaded data arrives from durable storage
## at runtime and is never baked into the image.
FROM python:3.12-slim

# System certs + curl for the container's HEALTHCHECK; no build toolchain needed
# (all Python deps ship manylinux wheels).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first (layer caches unless requirements.txt changes).
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --upgrade pip && pip install -r backend/requirements.txt

# App code only. Secrets (.env) and local state are excluded via .dockerignore;
# the OpenAI key arrives as a Container App secret at runtime.
COPY backend/ ./backend/
COPY VERSION ./VERSION

# uvicorn is launched from the backend dir so top-level modules (main, routers,
# system_db, app_config) resolve exactly as they do in local dev.
WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
