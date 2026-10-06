from fastapi import APIRouter, Depends, Response

from app.repositories import books
from app.schemas import BookIn
from app.security import require_api_key

router = APIRouter(prefix="/api/books")


@router.get("")
def list_books(q: str | None = None):
    return books.list_books(q.strip() if q else None)


@router.post("", status_code=201, dependencies=[Depends(require_api_key)])
def create_book(book: BookIn):
    return books.create(book)


@router.delete("/{book_id}", status_code=204, dependencies=[Depends(require_api_key)])
def delete_book(book_id: int):
    books.delete(book_id)
    return Response(status_code=204)
