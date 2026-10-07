"""Shared limit/offset query parameters."""
from dataclasses import dataclass

from fastapi import Query


@dataclass(frozen=True)
class Page:
    limit: int
    offset: int


def page_params(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page:
    return Page(limit=limit, offset=offset)
