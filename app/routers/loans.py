from fastapi import APIRouter, Depends

from app.repositories import loans
from app.schemas import LoanIn
from app.security import require_api_key

router = APIRouter(prefix="/api/loans")


@router.get("")
def list_loans(active: bool = False):
    return loans.list_loans(active)


@router.post("", status_code=201, dependencies=[Depends(require_api_key)])
def borrow(loan: LoanIn):
    return loans.borrow(loan.book_id, loan.member_id)


@router.post("/{loan_id}/return", dependencies=[Depends(require_api_key)])
def return_loan(loan_id: int):
    return loans.return_loan(loan_id)
