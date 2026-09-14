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

# Optionally bundle a pinned model outside the writable runtime cache so that
# mounting a fresh cache volume cannot hide the model shipped in the image.
ARG BUNDLE_PHOWHISPER_MODEL=false
ARG PHOWHISPER_BUNDLE_REVISION=7ebdb9e88f5cc5271fb88f4d642c82ff9388650e
COPY deploy/bundle_phowhisper.py /tmp/bundle_phowhisper.py
RUN if [ "$BUNDLE_PHOWHISPER_MODEL" = "true" ]; then \
        test "$INSTALL_PHOWHISPER" = "true" && \
        python /tmp/bundle_phowhisper.py "$PHOWHISPER_BUNDLE_REVISION"; \
    fi
ENV PHOWHISPER_BUNDLE_DIR=/opt/models/phowhisper

# Copy application files
COPY app/ ./app/
COPY README.md .env.example ./

# Security: run as non-root user in banking microservice
RUN useradd -u 10001 -m -s /bin/bash appuser && \
    mkdir -p /app/.cache/huggingface && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
