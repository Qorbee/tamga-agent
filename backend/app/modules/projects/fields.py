"""Per-field provenance: who set each editable field, and whether a new value may replace it
(ADR-0010). The owner's value is never overwritten by anything but the owner; a value that may not
replace the current one becomes a field proposal instead.

A target is anything with fields and a ``field_sources`` dict: a project here, a shipment in the
logistics pack. Each kind registers how an accepted proposal is written back, so this module never
needs to know the pack's tables.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.projects.models import FieldSource, ProposalTarget

# Owner, then confirmed extraction, then suggestion.
_RANK: Final = {FieldSource.SUGGESTION: 0, FieldSource.EXTRACTION: 1, FieldSource.OWNER: 2}

# Writes an accepted value onto one target, as the owner's: (db, company_id, target_id, field,
# value). Raises NotFound when the target is gone, BadRequest when the value no longer fits.
type AcceptWriter = Callable[[AsyncSession, uuid.UUID, uuid.UUID, str, Any], Awaitable[None]]

_writers: dict[ProposalTarget, AcceptWriter] = {}


def register_target(target: ProposalTarget, writer: AcceptWriter) -> None:
    _writers[target] = writer


def writer_for(target: ProposalTarget) -> AcceptWriter:
    try:
        return _writers[target]
    except KeyError:
        raise LookupError(f"no pack registered the {target} field target") from None


def may_replace(current: FieldSource | None, incoming: FieldSource) -> bool:
    return current is None or _RANK[incoming] >= _RANK[current]


def source_of(field_sources: dict[str, str], field: str) -> FieldSource | None:
    value = field_sources.get(field)
    return FieldSource(value) if value is not None else None
