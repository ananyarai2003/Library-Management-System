from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import config
from app.db import init_db
from app.errors import register_error_handlers
from app.logging_config import configure_logging, logger, register_request_logging
from app.routers import books, health, loans, members


def _validate_config() -> None:
    config.loan_days()
    config.db_timeout_seconds()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    logger.info("database initialised at %s", config.db_path())
    yield


def create_app() -> FastAPI:
    configure_logging()
    _validate_config()  # fail fast on bad environment values
    app = FastAPI(title="Library Management System", lifespan=lifespan)
    origins = config.cors_origins()
    if origins:  # same-origin only unless explicitly configured
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-API-Key"],
        )
    register_request_logging(app)
    register_error_handlers(app)
    for module in (health, books, members, loans):
        app.include_router(module.router)
    # Mounted last so /api routes take priority. Skipped when the frontend is not checked out (e.g. CI).
    if config.FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
    return app
