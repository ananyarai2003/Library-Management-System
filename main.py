import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DEFAULT_DB_PATH = Path(__file__).parent / "library.db"
DEFAULT_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@dataclass(frozen=True)
class Settings:
    db_path: Path
    frontend_dir: Path
    cors_origins: list[str]
    log_level: str
    environment: Literal["local", "dev", "staging", "prod"]
    jwt_secret: str
    jwt_issuer: str
    jwt_audience: str
    jwt_exp_minutes: int
    users_json: str | None


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _load_settings() -> Settings:
    env = os.getenv("ENVIRONMENT", "local").lower()
    environment: Literal["local", "dev", "staging", "prod"] = env if env in {"local", "dev", "staging", "prod"} else "local"

    db_path = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
    frontend_dir = Path(os.getenv("FRONTEND_DIR", str(DEFAULT_FRONTEND_DIR))).expanduser()
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    cors_origins = _parse_csv(os.getenv("CORS_ORIGINS"))
    # Only allow "*" automatically in local/dev; in staging/prod require explicit origins.
    if environment in {"local", "dev"} and not cors_origins:
        cors_origins = ["*"]
    elif environment in {"staging", "prod"} and (not cors_origins or cors_origins == ["*"]):
        cors_origins = []

    jwt_secret = os.getenv("JWT_SECRET", "dev-insecure-secret-change-me")  # do not use in prod
    jwt_issuer = os.getenv("JWT_ISSUER", "library-api")
    jwt_audience = os.getenv("JWT_AUDIENCE", "library-frontend")
    jwt_exp_minutes = int(os.getenv("JWT_EXP_MINUTES", "120"))
    users_json = os.getenv("USERS_JSON")

    return Settings(
        db_path=db_path,
        frontend_dir=frontend_dir,
        cors_origins=cors_origins,
        log_level=log_level,
        environment=environment,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
        jwt_exp_minutes=jwt_exp_minutes,
        users_json=users_json,
    )


settings = _load_settings()

logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))
logger = logging.getLogger("library")

app = FastAPI(title="Library Management System")

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )


@contextmanager
def get_db():
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate_db() -> None:
    """
    Lightweight sequential migrations stored in schema_migrations.
    This avoids adding an Alembic environment while still being reliable.
    """
    migrations: list[tuple[int, str]] = [
        (
            1,
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """,
        ),
        (
            2,
            """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                available INTEGER NOT NULL DEFAULT 1
            );
            """,
        ),
        (
            3,
            """
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                created_at TEXT NOT NULL
            );
            """,
        ),
        (
            4,
            """
            CREATE TABLE IF NOT EXISTS loans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                borrowed_at TEXT NOT NULL,
                due_date TEXT NOT NULL,
                returned_at TEXT,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE RESTRICT,
                FOREIGN KEY(member_id) REFERENCES members(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_loans_book_id ON loans(book_id);
            CREATE INDEX IF NOT EXISTS idx_loans_member_id ON loans(member_id);
            CREATE INDEX IF NOT EXISTS idx_loans_returned_at ON loans(returned_at);
            """,
        ),
        (
            5,
            """
            CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
            CREATE INDEX IF NOT EXISTS idx_books_author ON books(author);
            """,
        ),
    ]

    with get_db() as conn:
        # Ensure migration table exists
        conn.executescript(migrations[0][1])

        current = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations").fetchone()["v"]
        for version, sql in migrations[1:]:
            if version <= current:
                continue
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
            logger.info("Applied migration %s", version)


@app.on_event("startup")
def _startup() -> None:
    if settings.environment in {"staging", "prod"} and not settings.cors_origins:
        logger.warning("CORS_ORIGINS not configured; browser clients will be blocked in %s.", settings.environment)
    migrate_db()


class ErrorResponse(BaseModel):
    error: dict[str, Any]


def _error(message: str, *, code: str = "error", status_code: int = 400, details: Any | None = None) -> JSONResponse:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=payload)


@app.exception_handler(HTTPException)
def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    return _error(str(exc.detail), code="http_error", status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error("Validation error", code="validation_error", status_code=422, details=exc.errors())


@app.exception_handler(Exception)
def unhandled_exception_handler(_request: Request, _exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error")
    return _error("Internal server error", code="internal_error", status_code=500)


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    author: str = Field(min_length=1, max_length=500)


class BookPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    author: Optional[str] = Field(default=None, min_length=1, max_length=500)


class MemberIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: Optional[str] = Field(default=None, max_length=320)


class LoanIn(BaseModel):
    book_id: int
    member_id: int
    due_date: date


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=500)


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + pad).encode("ascii"))


def _jwt_sign(secret: str, msg: bytes) -> str:
    return _b64url_encode(hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest())


def jwt_encode(payload: dict[str, Any], *, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = _jwt_sign(secret, signing_input)
    return f"{header_b64}.{payload_b64}.{sig}"


def jwt_decode(token: str, *, secret: str) -> dict[str, Any]:
    try:
        header_b64, payload_b64, sig = token.split(".")
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from e

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = _jwt_sign(secret, signing_input)
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token signature")

    payload = json.loads(_b64url_decode(payload_b64))
    exp = payload.get("exp")
    if exp is None or time.time() > float(exp):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")

    if payload.get("iss") != settings.jwt_issuer:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token issuer")
    if payload.get("aud") != settings.jwt_audience:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token audience")

    return payload


def _pbkdf2_hash(password: str, *, salt: str, iterations: int = 120_000) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${base64.b64encode(dk).decode('ascii')}"


def _pbkdf2_verify(password: str, stored: str) -> bool:
    try:
        algo, iters_s, salt, b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iters)
        return hmac.compare_digest(base64.b64decode(b64), candidate)
    except Exception:
        return False


def _load_users() -> dict[str, dict[str, Any]]:
    """
    USERS_JSON format:
      [{"username":"admin","password_hash":"...","roles":["admin"]}, ...]
    If missing, use demo users with non-secret passwords. Do not use in production.
    """
    if settings.users_json:
        try:
            raw = json.loads(settings.users_json)
            return {u["username"]: {"password_hash": u["password_hash"], "roles": u.get("roles", [])} for u in raw}
        except Exception:
            logger.warning("Invalid USERS_JSON; falling back to demo users")

    # Demo users (no secrets). Intended for local/dev only.
    demo_salt = "demo-salt"
    return {
        "admin": {"password_hash": _pbkdf2_hash("admin", salt=demo_salt), "roles": ["admin"]},
        "librarian": {"password_hash": _pbkdf2_hash("librarian", salt=demo_salt), "roles": ["librarian"]},
    }


USERS = _load_users()


def _create_access_token(username: str, roles: list[str]) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.jwt_exp_minutes)
    payload = {
        "sub": username,
        "roles": roles,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt_encode(payload, secret=settings.jwt_secret)


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict[str, Any]:
    payload = jwt_decode(token, secret=settings.jwt_secret)
    return {"username": payload.get("sub"), "roles": payload.get("roles", [])}


def require_roles(*required: str):
    def _dep(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        roles = set(user.get("roles") or [])
        if not roles.intersection(required):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return _dep


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "environment": settings.environment}


@app.get("/ready")
def ready() -> dict[str, Any]:
    try:
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"ready": True}
    except Exception:
        return {"ready": False}


@app.post("/api/auth/login")
def login(data: LoginIn) -> dict[str, Any]:
    user = USERS.get(data.username)
    if not user or not _pbkdf2_verify(data.password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    token = _create_access_token(data.username, user.get("roles", []))
    return {"access_token": token, "token_type": "bearer"}


@app.get("/api/books")
def list_books(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, min_length=1, max_length=200),
):
    where = ""
    params: list[Any] = []
    if q:
        where = "WHERE title LIKE ? OR author LIKE ?"
        like = f"%{q}%"
        params.extend([like, like])

    sql = f"SELECT * FROM books {where} ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [dict(r) | {"available": bool(r["available"])} for r in rows]


@app.post("/api/books", status_code=201, dependencies=[Depends(require_roles("admin", "librarian"))])
def add_book(book: BookIn):
    with get_db() as conn:
        cur = conn.execute("INSERT INTO books (title, author) VALUES (?, ?)", (book.title, book.author))
        row = conn.execute("SELECT * FROM books WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row) | {"available": bool(row["available"])}


@app.patch("/api/books/{book_id}", dependencies=[Depends(require_roles("admin", "librarian"))])
def update_book(book_id: int, patch: BookPatch):
    fields: list[str] = []
    params: list[Any] = []
    if patch.title is not None:
        fields.append("title = ?")
        params.append(patch.title)
    if patch.author is not None:
        fields.append("author = ?")
        params.append(patch.author)

    if not fields:
        raise HTTPException(400, "No fields to update")

    params.append(book_id)
    with get_db() as conn:
        cur = conn.execute(f"UPDATE books SET {', '.join(fields)} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise HTTPException(404, "Book not found")
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return dict(row) | {"available": bool(row["available"])}


@app.post("/api/books/{book_id}/toggle", dependencies=[Depends(require_roles("admin", "librarian"))])
def toggle_availability(book_id: int):
    with get_db() as conn:
        cur = conn.execute("UPDATE books SET available = 1 - available WHERE id = ?", (book_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Book not found")
    return {"ok": True}


@app.delete("/api/books/{book_id}", dependencies=[Depends(require_roles("admin"))])
def delete_book(book_id: int):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Book not found")
    return {"ok": True}


@app.get("/api/members")
def list_members(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM members ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/members", status_code=201, dependencies=[Depends(require_roles("admin", "librarian"))])
def add_member(member: MemberIn):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO members (name, email, created_at) VALUES (?, ?, ?)",
            (member.name, member.email, now),
        )
        row = conn.execute("SELECT * FROM members WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


@app.post("/api/loans/borrow", status_code=201, dependencies=[Depends(require_roles("admin", "librarian"))])
def borrow_book(payload: LoanIn):
    borrowed_at = datetime.now(timezone.utc).isoformat()
    due_iso = datetime.combine(payload.due_date, datetime.min.time(), tzinfo=timezone.utc).isoformat()

    with get_db() as conn:
        book = conn.execute("SELECT * FROM books WHERE id = ?", (payload.book_id,)).fetchone()
        if not book:
            raise HTTPException(404, "Book not found")
        if int(book["available"]) != 1:
            raise HTTPException(409, "Book is not available")

        member = conn.execute("SELECT * FROM members WHERE id = ?", (payload.member_id,)).fetchone()
        if not member:
            raise HTTPException(404, "Member not found")

        # Mark book unavailable and create loan atomically in same transaction.
        conn.execute("UPDATE books SET available = 0 WHERE id = ?", (payload.book_id,))
        cur = conn.execute(
            "INSERT INTO loans (book_id, member_id, borrowed_at, due_date, returned_at) VALUES (?, ?, ?, ?, NULL)",
            (payload.book_id, payload.member_id, borrowed_at, due_iso),
        )
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (cur.lastrowid,)).fetchone()

    return dict(loan)


@app.post("/api/loans/{loan_id}/return", dependencies=[Depends(require_roles("admin", "librarian"))])
def return_book(loan_id: int):
    returned_at = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if not loan:
            raise HTTPException(404, "Loan not found")
        if loan["returned_at"] is not None:
            raise HTTPException(409, "Loan already returned")

        conn.execute("UPDATE loans SET returned_at = ? WHERE id = ?", (returned_at, loan_id))
        conn.execute("UPDATE books SET available = 1 WHERE id = ?", (loan["book_id"],))
        updated = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    return dict(updated)


# Serve the frontend at / (mounted last so /api routes take priority)
app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
