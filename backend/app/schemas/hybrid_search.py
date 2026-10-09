"""Request and response schemas for tenant-scoped hybrid search."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.retrieval.schemas import HybridCandidate


class HybridSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=10_000)
    dense_candidate_limit: int = Field(default=20, ge=1, le=100)
    lexical_candidate_limit: int = Field(default=20, ge=1, le=100)
    top_k: int = Field(default=10, ge=1, le=100)
    rrf_k: int = Field(default=60, gt=0)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class HybridSearchResponse(BaseModel):
    results: list[HybridCandidate]
