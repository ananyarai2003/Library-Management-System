from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import config
from app.db import init_db
from app.errors import register_error_handlers
from app.routers import books, health, loans, members


def create_app() -> FastAPI:
    app = FastAPI(title="Library Management System")
    origins = config.cors_origins()
    if origins:  # same-origin only unless explicitly configured
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "X-API-Key"],
        )
    register_error_handlers(app)
    for module in (health, books, members, loans):
        app.include_router(module.router)
    init_db()
    # Mounted last so /api routes take priority.
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
    return app
