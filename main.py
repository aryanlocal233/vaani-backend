"""Vaani translation backend — FastAPI application entry point."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import redis.asyncio as redis
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from api.health import router as health_router
from api.websocket_handler import router as websocket_router
from config import get_settings
from pipeline.orchestrator import Orchestrator
from services.bhashini_client import BhashiniClient
from services.nmt_service import NMTService
from services.stt_service import STTService
from services.tts_service import TTSService
from session.session_manager import SessionManager


def configure_logging(log_level: str) -> None:
    logging.basicConfig(level=log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(log_level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)

    logger.info("startup_begin")

    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    await redis_client.ping()
    logger.info("redis_connected", url=settings.REDIS_URL)

    bhashini_client = BhashiniClient(settings)

    stt_service = STTService(bhashini_client, settings)
    nmt_service = NMTService(bhashini_client, settings)
    tts_service = TTSService(bhashini_client, settings)
    orchestrator = Orchestrator(stt_service, nmt_service, tts_service)
    session_manager = SessionManager(redis_client, ttl_seconds=settings.SESSION_TTL_SECONDS)

    app.state.settings = settings
    app.state.redis = redis_client
    app.state.bhashini_client = bhashini_client
    app.state.stt_service = stt_service
    app.state.nmt_service = nmt_service
    app.state.tts_service = tts_service
    app.state.orchestrator = orchestrator
    app.state.session_manager = session_manager

    logger.info("startup_complete")
    yield

    logger.info("shutdown_begin")
    await bhashini_client.aclose()
    await redis_client.aclose()
    logger.info("shutdown_complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="Vaani Translation Backend", version="1.0.0", lifespan=lifespan)

    origins = ["*"] if settings.CORS_ALLOW_ORIGINS == "*" else settings.CORS_ALLOW_ORIGINS.split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        return response

    app.include_router(health_router)
    app.include_router(websocket_router)

    return app


app = create_app()
