import sqlite3

from fastapi import APIRouter, Depends, Query, Response

from app.db import get_conn
from app.repositories import members as repo
from app.schemas import MemberIn, MemberOut, MemberUpdate
from app.security import WriteAccess

router = APIRouter(prefix="/api/members", tags=["members"])


@router.get("", response_model=list[MemberOut])
def list_members(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    conn: sqlite3.Connection = Depends(get_conn),
):
    return repo.list_members(conn, limit, offset)


@router.get("/{member_id}", response_model=MemberOut)
def get_member(member_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return repo.get_member(conn, member_id)


@router.post("", response_model=MemberOut, status_code=201, dependencies=[WriteAccess])
def add_member(member: MemberIn, conn: sqlite3.Connection = Depends(get_conn)):
    return repo.create_member(conn, member.model_dump())


@router.patch("/{member_id}", response_model=MemberOut, dependencies=[WriteAccess])
def edit_member(member_id: int, member: MemberUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    return repo.update_member(conn, member_id, member.model_dump(exclude_unset=True))


@router.delete("/{member_id}", status_code=204, dependencies=[WriteAccess])
def delete_member(member_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    repo.delete_member(conn, member_id)
    return Response(status_code=204)
