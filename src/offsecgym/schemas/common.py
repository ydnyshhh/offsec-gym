"""Shared schema conventions and stable identifiers."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field

SchemaVersion = Annotated[str, Field(pattern=r"^1$")]


class StrictModel(BaseModel):
    """Frozen external contract; unknown fields cannot silently alter a run."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def new_id() -> UUID:
    return uuid4()


def derived_id(namespace: UUID, *parts: str) -> UUID:
    """Stable ID for compiler-owned entities, never for run secrets."""

    return uuid5(namespace, "/".join(parts))
