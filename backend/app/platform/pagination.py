"""Paging for list endpoints: every list is a page of a generic resource with its total, never a
shape made for one screen (root AGENTS.md, contract 3)."""

from typing import Annotated, Final

from fastapi import Depends, Query
from pydantic import BaseModel

MAX_LIMIT: Final = 200


class PageParams(BaseModel):
    limit: int
    offset: int


def page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


PageDep = Annotated[PageParams, Depends(page_params)]


def escape_like(text: str) -> str:
    """User text inside a LIKE pattern, with its own % and _ taken literally (ESCAPE '\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
