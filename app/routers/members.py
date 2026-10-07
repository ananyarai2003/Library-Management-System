from fastapi import APIRouter, Depends

from app.pagination import Page, page_params
from app.repositories import members
from app.schemas import MemberIn
from app.security import require_api_key

router = APIRouter(prefix="/api/members")


@router.get("")
def list_members(page: Page = Depends(page_params)):
    return members.list_members(page)


@router.post("", status_code=201, dependencies=[Depends(require_api_key)])
def create_member(member: MemberIn):
    return members.create(member)
