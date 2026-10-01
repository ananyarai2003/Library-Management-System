import sqlite3

from fastapi import APIRouter, Depends, Query, Response

from app.db import get_conn
from app.repositories import books as repo
from app.schemas import BookIn, BookOut, BookUpdate
from app.security import WriteAccess

router = APIRouter(prefix="/api/books", tags=["books"])


@router.get("", response_model=list[BookOut])
def list_books(
    q: str | None = Query(default=None, max_length=100, description="Search title, author or ISBN"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    conn: sqlite3.Connection = Depends(get_conn),
):
    return repo.list_books(conn, q.strip() if q else None, limit, offset)


@router.get("/{book_id}", response_model=BookOut)
def get_book(book_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return repo.get_book(conn, book_id)


@router.post("", response_model=BookOut, status_code=201, dependencies=[WriteAccess])
def add_book(book: BookIn, conn: sqlite3.Connection = Depends(get_conn)):
    return repo.create_book(conn, book.model_dump())


@router.patch("/{book_id}", response_model=BookOut, dependencies=[WriteAccess])
def edit_book(book_id: int, book: BookUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    return repo.update_book(conn, book_id, book.model_dump(exclude_unset=True))


@router.delete("/{book_id}", status_code=204, dependencies=[WriteAccess])
def delete_book(book_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    repo.delete_book(conn, book_id)
    return Response(status_code=204)
