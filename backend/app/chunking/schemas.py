"""Typed internal representation for retrieval-ready canonical chunks."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChunkSourceData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_element_id: UUID
    source_ref: str
    element_type: str
    element_ordinal: int = Field(ge=0)
    location_ordinal: int = Field(ge=0)
    page_id: UUID | None = None
    page_number: int | None = Field(default=None, gt=0)


class DocumentChunkData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: UUID
    tenant_id: UUID
    document_id: UUID
    document_version_id: UUID
    section_id: UUID | None = None
    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1)
    character_length: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources: list[ChunkSourceData] = Field(min_length=1)
