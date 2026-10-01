"""Application factory."""
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.db import connect, migrate
from app.errors import register_error_handlers
from app.routers import books, health, loans, members

logger = logging.getLogger("lms")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="Library Management System", version="2.0.0")
    app.state.settings = settings

    conn = connect(settings.db_path)
    try:
        migrate(conn)
    finally:
        conn.close()
    if not settings.api_key:
        logger.warning("LMS_API_KEY is not set: all write endpoints will respond 503")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("%s %s -> %s (%.1f ms)", request.method, request.url.path, response.status_code, elapsed_ms)
        return response

    register_error_handlers(app)
    for module in (health, books, members, loans):
        app.include_router(module.router)
    # Mounted last so /api and health routes take priority.
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app
