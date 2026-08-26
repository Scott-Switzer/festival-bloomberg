# Festival Intelligence Acquisition Container
# P0.4 fix: Long-running entrypoint for Container DO exec().
# Tasks are executed via ctx.container.exec(), not ENTRYPOINT args.

FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir httpx

# Copy the Python package
COPY python/ python/
ENV PYTHONPATH=/app/python

# Copy acquisition scripts
COPY scripts/ scripts/

# Copy cloud runtime entrypoint
COPY cloud-runtime/docker/cloud_entrypoint.py .

# Copy schema migrations
COPY schema/ schema/

# Non-root user
RUN useradd -m -r collector && chown -R collector:collector /app
USER collector

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import duckdb; print('ok')" || exit 1

# Environment (secrets injected at runtime via Cloudflare bindings)
ENV FI_OBJECT_STORE=R2
ENV FI_R2_RAW_BUCKET=festival-intelligence-raw
ENV FI_R2_LAKE_BUCKET=festival-intelligence-lake
ENV FI_R2_BACKUP_BUCKET=festival-intelligence-backups
ENV PYTHONUNBUFFERED=1

# P0.4: Long-running idle entrypoint.
# Container DO calls this, then exec() invokes cloud_entrypoint.py per task.
# This keeps the container alive and ready for exec() calls.
CMD ["python", "-c", "import time; print('Container ready'); time.sleep(86400)"]
