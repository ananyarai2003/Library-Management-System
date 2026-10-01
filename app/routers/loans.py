import sqlite3

from fastapi import APIRouter, Depends, Query, Request

from app.db import get_conn
from app.repositories import loans as repo
from app.schemas import LoanIn, LoanOut
from app.security import WriteAccess

router = APIRouter(prefix="/api/loans", tags=["loans"])


@router.get("", response_model=list[LoanOut])
def list_loans(
    member_id: int | None = Query(default=None, gt=0),
    book_id: int | None = Query(default=None, gt=0),
    active: bool | None = Query(default=None, description="true = not yet returned"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    conn: sqlite3.Connection = Depends(get_conn),
):
    return repo.list_loans(conn, member_id, book_id, active, limit, offset)


@router.post("", response_model=LoanOut, status_code=201, dependencies=[WriteAccess])
def borrow_book(loan: LoanIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return repo.borrow(conn, loan.book_id, loan.member_id, request.app.state.settings.loan_days)


@router.post("/{loan_id}/return", response_model=LoanOut, dependencies=[WriteAccess])
def return_book(loan_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return repo.return_loan(conn, loan_id)
