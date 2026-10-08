"""Typed provider-neutral representation of an ingested document."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class DocumentElementType(StrEnum):
    TEXT = "TEXT"
    TABLE = "TABLE"
    FIGURE = "FIGURE"


class InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoundingBoxData(InternalModel):
    left: float = Field(alias="l")
    top: float = Field(alias="t")
    right: float = Field(alias="r")
    bottom: float = Field(alias="b")
    coordinate_origin: str | None = Field(default=None, alias="coord_origin")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ProvenanceLocation(InternalModel):
    """One parser-reported location; absent page numbers stay null."""

    page_number: PositiveInt | None = None
    bounding_box: BoundingBoxData | None = None
    character_span: tuple[int, int] | None = None


class DocumentPageData(InternalModel):
    page_number: PositiveInt
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)


class DocumentSectionData(InternalModel):
    ordinal: int = Field(ge=0)
    source_ref: str = Field(min_length=1, max_length=200)
    title: str
    level: PositiveInt
    parent_source_ref: str | None = Field(default=None, max_length=200)
    provenance: list[ProvenanceLocation] = Field(default_factory=list)


class DocumentElementData(InternalModel):
    ordinal: int = Field(ge=0)
    source_ref: str = Field(min_length=1, max_length=200)
    element_type: DocumentElementType
    text_content: str | None = None
    structured_content: dict[str, Any] | None = None
    section_source_ref: str | None = Field(default=None, max_length=200)
    provenance: list[ProvenanceLocation] = Field(default_factory=list)


class DocumentMetadata(InternalModel):
    source_filename: str = Field(min_length=1, max_length=255)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_name: str = "docling"
    parser_version: str = Field(min_length=1, max_length=40)
    document_name: str | None = None
    page_count: int = Field(ge=0)


class DocumentStructure(InternalModel):
    metadata: DocumentMetadata
    pages: list[DocumentPageData] = Field(default_factory=list)
    sections: list[DocumentSectionData] = Field(default_factory=list)
    elements: list[DocumentElementData] = Field(default_factory=list)
