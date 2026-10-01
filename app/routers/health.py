import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.db import get_conn

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(conn: sqlite3.Connection = Depends(get_conn)):
    """Readiness: the database answers queries."""
    try:
        conn.execute("SELECT 1").fetchone()
    except sqlite3.Error as exc:
        raise HTTPException(503, "Database unavailable") from exc
    return {"status": "ready"}
