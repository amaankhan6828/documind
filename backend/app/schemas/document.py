"""Logical document request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _clean_required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value must not be blank")
    return value


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError("Value must not be blank")
    return value


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    document_type: str = Field(min_length=1, max_length=100)
    issuer: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    _validate_title = field_validator("title")(_clean_required_text)
    _validate_document_type = field_validator("document_type")(_clean_required_text)
    _validate_issuer = field_validator("issuer")(_clean_optional_text)
    _validate_description = field_validator("description")(_clean_optional_text)


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    document_type: str | None = Field(default=None, min_length=1, max_length=100)
    issuer: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    _validate_title = field_validator("title")(_clean_optional_text)
    _validate_document_type = field_validator("document_type")(_clean_optional_text)
    _validate_issuer = field_validator("issuer")(_clean_optional_text)
    _validate_description = field_validator("description")(_clean_optional_text)

    @model_validator(mode="after")
    def require_update_fields(self) -> "DocumentUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one metadata field must be provided")
        if any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in ("title", "document_type")
        ):
            raise ValueError("title and document_type cannot be null")
        return self


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    document_type: str | None
    issuer: str | None
    description: str | None
    created_at: datetime
    updated_at: datetime
