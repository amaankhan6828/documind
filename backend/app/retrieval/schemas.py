"""Typed records exchanged by embedding, indexing, and retrieval services."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_element_id: UUID
    source_ref: str
    element_type: str
    page_id: UUID | None = None
    page_number: int | None = Field(default=None, gt=0)


class IndexRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: UUID
    tenant_id: UUID
    document_id: UUID
    document_version_id: UUID
    section_id: UUID | None = None
    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_type: str | None = None
    issuer: str | None = None
    sources: list[SourceReference] = Field(default_factory=list)


class DenseCandidate(BaseModel):
    """A Qdrant hit; canonical chunk text can be fetched from PostgreSQL by ID."""

    chunk_id: UUID
    score: float
    payload: dict
