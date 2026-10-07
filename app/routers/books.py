from fastapi import APIRouter, Depends, Response

from app.pagination import Page, page_params
from app.repositories import books
from app.schemas import AvailabilityIn, BookIn
from app.security import require_api_key

router = APIRouter(prefix="/api/books")


@router.get("")
def list_books(q: str | None = None, available: bool | None = None, page: Page = Depends(page_params)):
    return books.list_books(q.strip() if q else None, available, page)


@router.post("", status_code=201, dependencies=[Depends(require_api_key)])
def create_book(book: BookIn):
    return books.create(book)


@router.patch("/{book_id}/availability", dependencies=[Depends(require_api_key)])
def set_availability(book_id: int, body: AvailabilityIn):
    return books.set_availability(book_id, body.available_copies)


@router.delete("/{book_id}", status_code=204, dependencies=[Depends(require_api_key)])
def delete_book(book_id: int):
    books.delete(book_id)
    return Response(status_code=204)
