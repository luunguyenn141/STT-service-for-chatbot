FROM python:3.11-slim

# Prevent python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface

# Install system dependencies (ffmpeg is essential for audio transcoding)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirement files first for layer caching
COPY requirements.txt requirements-phowhisper.txt ./

# Build argument: set to "true" to include local PhoWhisper dependencies (PyTorch, Transformers)
ARG INSTALL_PHOWHISPER=false
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    if [ "$INSTALL_PHOWHISPER" = "true" ]; then \
        pip install "torch>=2.2,<3" --index-url "$PYTORCH_INDEX_URL" && \
        pip install -r requirements-phowhisper.txt; \
    fi

# Copy application files
COPY app/ ./app/
COPY README.md .env.example ./

# Security: run as non-root user in banking microservice
RUN useradd -u 10001 -m -s /bin/bash appuser && \
    mkdir -p /app/.cache/huggingface && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
