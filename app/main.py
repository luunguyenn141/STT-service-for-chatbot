from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartParser

from app.api.health import router as health_router
from app.api.transcriptions import router as transcription_router
from app.config import get_settings
from app.services.stt.phowhisper import preload_phowhisper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("stt_poc")

BASE_DIR = Path(__file__).resolve().parent
settings = get_settings()
# The POC permits uploads up to MAX_UPLOAD_MB, so keep those small, bounded
# uploads in memory instead of falling back to an operating-system temp file.
MultiPartParser.spool_max_size = settings.max_upload_bytes


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.stt_provider == "phowhisper" and settings.phowhisper_preload:
        logger.info(
            "preloading_phowhisper model=%s device=%s",
            settings.phowhisper_model_id,
            settings.phowhisper_device,
        )
        await asyncio.to_thread(
            preload_phowhisper,
            settings.phowhisper_model_id,
            settings.phowhisper_device,
        )
        logger.info("phowhisper_preload_complete model=%s", settings.phowhisper_model_id)
    yield


app = FastAPI(
    title="Vietnamese STT POC",
    version="0.1.0",
    description="A browser-testable Vietnamese Speech-to-Text proof of concept.",
    lifespan=lifespan,
)


class DynamicCORSMiddleware(CORSMiddleware):
    """CORS middleware that dynamically looks up allowed origins from current settings."""

    def is_allowed_origin(self, origin: str) -> bool:
        current_origins = get_settings().cors_origins
        if "*" in current_origins:
            return True
        return origin in current_origins


app.add_middleware(
    DynamicCORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "x-api-key", "x-request-id", "Accept"],
    expose_headers=["x-request-id", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(health_router)
app.include_router(transcription_router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    request.state.request_id = request_id

    forwarded_for = request.headers.get("x-forwarded-for")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (request.client.host if request.client else "unknown")
    request.state.client_ip = client_ip

    started_at = time.perf_counter()
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    duration_ms = round((time.perf_counter() - started_at) * 1_000)
    logger.info(
        "request_completed request_id=%s client_ip=%s method=%s path=%s status=%s duration_ms=%s",
        request_id,
        client_ip,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")
