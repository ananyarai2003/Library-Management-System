# Library Management System — backend

FastAPI + SQLite service. Serves the JSON API under `/api` and, the static UI from `frontend/` at `/`.

## Run

```
pip install -r requirements-dev.txt
pytest -q
ruff check .
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 (API docs at `/docs`). `main.py` in this directory is the legacy single-file version; use `app.main:app`.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `LMS_DB_PATH` | `backend/library.db` | SQLite file |
| `LMS_DB_TIMEOUT` | `5` | Seconds to wait on a locked database |
| `LMS_API_KEY` | unset | When set, write endpoints require header `X-API-Key`. **Set this in production**; unset means writes are open. |
| `LMS_CORS_ORIGINS` | unset | Comma-separated allowed origins. Unset = no CORS (same-origin only). Methods limited to GET/POST/PATCH/DELETE. |
| `LMS_LOAN_DAYS` | `14` | Loan period |
| `LMS_LOG_LEVEL` | `INFO` | Log level |

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | Liveness |
| GET | `/api/books` | `q`, `available=true\|false`, `limit` (1-500, default 100), `offset` |
| POST | `/api/books` | Write (API key) |
| PATCH | `/api/books/{id}/availability` | Body `{"available_copies": n}`, `0 <= n <= total_copies`. Write (API key) |
| DELETE | `/api/books/{id}` | Write; 409 if the book has loan records |
| GET / POST | `/api/members` | `limit`, `offset` on GET |
| GET | `/api/loans` | `active`, `member_id`, `book_id`, `limit`, `offset` |
| POST | `/api/loans` | Borrow; 409 when no copies are available |
| POST | `/api/loans/{id}/return` | Return a loan |

Errors share one shape: `{"error": {"message": "...", "details": [...]}}`.

## Runbook

- **Logs:** one JSON object per line on stderr (`time`, `level`, `message`, and for requests `request_id`, `method`, `path`, `status`, `duration_ms`). Send `X-Request-ID` to correlate; it is echoed in the response, and generated if absent.
- **Database:** created and migrated on startup. WAL mode is enabled, so `library.db-wal` and `library.db-shm` appear next to the database. Back up with `sqlite3 library.db ".backup backup.db"`, not a plain file copy while the service runs.
- **"database is locked" / 5xx under load:** raise `LMS_DB_TIMEOUT`; writes are serialised (`BEGIN IMMEDIATE`).
- **Auth:** single shared API key, compared in constant time. Rotate by changing `LMS_API_KEY` and restarting. Reads are public.
- **CI:** `.github/workflows/ci.yml` runs `ruff check` and `pytest` on every push and PR.

## Layout and end-to-end tests

- `app/` — API, `tests/` — in-process pytest suite, `frontend/` — static HTML/CSS/JS served at `/`.
- `e2e/` — Playwright (Python) API and UI tests that start real uvicorn servers on temporary databases. Needs `playwright` and its Chromium browser installed (`py -m playwright install chromium`); not run in CI. Run with `cd e2e && py -m pytest -q` (about 4 minutes).
