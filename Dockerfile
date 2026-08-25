# QSSC API — containerised FastAPI backend (qssc simulator + Qiskit Aer).
# Host-agnostic: deploys as-is on Render, Fly.io, Hugging Face Spaces, Koyeb,
# or any container host. The host provides PORT (or 8000). Without Redis/Mongo
# the job queue falls back to an in-process executor (see api/jobs.py), so a
# single container is fully functional with no external services.
#
# Memory: import ~90 MB, a cold Shor-15 run ~100 MB, the heaviest density-matrix
# path (<=10 qubits) adds ~16 MB — comfortably fits a 512 MB free tier.
FROM python:3.12-slim

# Build tools for any source-built wheels; keep the image lean.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the package + API extras first (better layer caching).
COPY pyproject.toml README.md /app/
COPY qssc /app/qssc
COPY api /app/api
RUN pip install --no-cache-dir -e ".[api]"

# Render (and most hosts) inject PORT. Default to 8000 for local runs.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]