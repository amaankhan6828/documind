"""Environment-backed application settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or the root .env file."""

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://localhost:5432/documind"
    jwt_secret_key: SecretStr
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=30, gt=0, le=1440)
    document_storage_path: Path = Path("./storage/documents")
    max_upload_size_mb: int = Field(default=20, gt=0, le=1024)
    redis_url: str = "redis://localhost:6379/0"
    chunk_max_characters: int = Field(default=3000, ge=256, le=100000)
    chunk_overlap_characters: int = Field(default=200, ge=0, le=99999)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "documind_chunks"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = Field(default=384, gt=0, le=65536)
    index_batch_size: int = Field(default=64, gt=0, le=4096)

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> "Settings":
        if self.chunk_overlap_characters >= self.chunk_max_characters:
            raise ValueError("chunk overlap must be smaller than the chunk size")
        return self

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("JWT secret key must contain at least 32 bytes")
        return value

    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
