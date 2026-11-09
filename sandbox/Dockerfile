# DataReflex one-shot analysis sandbox (defaults.md §5).
# Build:  docker build -t data-reflex-sandbox:3.11 -f sandbox/Dockerfile sandbox
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# pandas for Python ops; SQL path uses stdlib sqlite3 + csv (no duckdb required).
RUN pip install --no-cache-dir "pandas==2.2.3"

WORKDIR /sandbox
# Runtime user is set by DockerSandboxExecutor (nobody); keep image root-owned readable.
