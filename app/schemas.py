"""Pydantic request/response models."""
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Isbn = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=17)]
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]


class _NoNullUpdate(BaseModel):
    """PATCH models: fields may be omitted, but required columns may not be set to null."""

    model_config = ConfigDict(extra="forbid")
    non_nullable: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _reject_nulls(self):
        for name in self.non_nullable:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class BookIn(BaseModel):
    title: Text
    author: Text
    isbn: Isbn | None = None
    total_copies: int = Field(default=1, ge=1, le=1000)


class BookUpdate(_NoNullUpdate):
    non_nullable = ("title", "author", "total_copies")
    title: Text | None = None
    author: Text | None = None
    isbn: Isbn | None = None
    total_copies: int | None = Field(default=None, ge=1, le=1000)


class BookOut(BaseModel):
    id: int
    title: str
    author: str
    isbn: str | None
    total_copies: int
    available_copies: int
    available: bool


class MemberIn(BaseModel):
    name: Text
    email: Email


class MemberUpdate(_NoNullUpdate):
    non_nullable = ("name", "email")
    name: Text | None = None
    email: Email | None = None


class MemberOut(BaseModel):
    id: int
    name: str
    email: str


class LoanIn(BaseModel):
    book_id: int = Field(gt=0)
    member_id: int = Field(gt=0)


class LoanOut(BaseModel):
    id: int
    book_id: int
    member_id: int
    loaned_at: str
    due_at: str
    returned_at: str | None
    overdue: bool
