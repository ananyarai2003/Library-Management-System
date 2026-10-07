"""Structured (one JSON object per line) logging and request-logging middleware."""
import json
import logging
import time
import uuid

from fastapi import FastAPI, Request

from app import config

logger = logging.getLogger("lms")

_EXTRA_FIELDS = ("request_id", "method", "path", "status", "duration_ms")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update({k: getattr(record, k) for k in _EXTRA_FIELDS if hasattr(record, k)})
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging() -> None:
    if any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        return  # already configured (create_app is called once per test)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(config.log_level())
    logger.propagate = False


def register_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        started = time.perf_counter()
        context = {"request_id": request_id, "method": request.method, "path": request.url.path}
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled error", extra=context)
            raise
        response.headers["X-Request-ID"] = request_id
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        logger.info("request", extra=context | {"status": response.status_code, "duration_ms": duration_ms})
        return response
