"""Request validation models."""
import re

from pydantic import BaseModel, Field, field_validator

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ISBN = re.compile(r"^[0-9Xx-]{10,17}$")


def _strip_nonblank(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("must not be blank")
    return v


class BookIn(BaseModel):
    title: str = Field(max_length=200)
    author: str = Field(max_length=200)
    isbn: str | None = None
    total_copies: int = Field(default=1, ge=1, le=1000)

    _clean_text = field_validator("title", "author")(lambda cls, v: _strip_nonblank(v))

    @field_validator("isbn")
    @classmethod
    def _isbn(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not _ISBN.match(v):
            raise ValueError("must be 10-17 characters: digits, X or hyphens")
        return v


class MemberIn(BaseModel):
    name: str = Field(max_length=100)
    email: str = Field(max_length=254)

    _clean_name = field_validator("name")(lambda cls, v: _strip_nonblank(v))

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL.match(v):
            raise ValueError("must be a valid email address")
        return v


class LoanIn(BaseModel):
    book_id: int = Field(ge=1)
    member_id: int = Field(ge=1)


class AvailabilityIn(BaseModel):
    available_copies: int = Field(ge=0, le=1000)
