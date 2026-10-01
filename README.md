# Library Management System

## Backend configuration

This backend is a FastAPI application serving a small REST API under `/api/books` and (optionally) a static frontend at `/`.

### Authentication (MVP)

Write endpoints require an API key.

- Header: `X-API-Key`
- Environment variable: `API_KEY`

Endpoints requiring auth:
- `POST /api/books`
- `POST /api/books/{book_id}/toggle`
- `DELETE /api/books/{book_id}`

Read endpoint is public:
- `GET /api/books`

Example:

```bash
export API_KEY="dev-secret"

curl -X POST http://127.0.0.1:8000/api/books \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-secret" \
  -d '{"title":"Clean Code","author":"Robert C. Martin"}'
```

### CORS

CORS is restricted to a configured allow-list.

- Environment variable: `ALLOWED_ORIGINS`
- Format: comma-separated origins

Example:

```bash
export ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
```

Allowed methods are limited to: `GET`, `POST`, `DELETE`.
Allowed headers are limited to: `Content-Type`, `X-API-Key`.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export API_KEY="dev-secret"
export ALLOWED_ORIGINS="http://localhost:3000"

uvicorn main:app --reload
```
