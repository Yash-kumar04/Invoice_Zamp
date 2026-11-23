# Dockerfile for Zamp Invoice Decision Engine per SPEC B13
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    AS_OF_DATE=2026-11-22

WORKDIR /app

# Install system dependencies for document handling and healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source and assets
COPY app/ ./app/
COPY config/ ./config/
COPY scenarios/ ./scenarios/
COPY samples/ ./samples/
COPY fixtures/ ./fixtures/
COPY static/ ./static/
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY SPEC.md DECISIONS.md README.md ./

EXPOSE ${PORT}

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/healthz || exit 1

# Launch uvicorn listening on $PORT per SPEC B13
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
