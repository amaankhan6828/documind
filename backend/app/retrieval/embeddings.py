"""Provider-neutral embedding interface and local Sentence Transformers adapter."""

from functools import lru_cache
from typing import Protocol, Sequence

from backend.app.core.config import get_settings


class EmbeddingProvider(Protocol):
    """Minimal contract required by indexing and query embedding."""

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class SentenceTransformersProvider:
    """CPU-backed local model adapter; no hosted embedding API is used."""

    def __init__(self, model_name: str, expected_dimension: int) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name, device="cpu")
        dimension_reader = getattr(self._model, "get_embedding_dimension", None)
        if dimension_reader is None:
            dimension_reader = self._model.get_sentence_embedding_dimension
        actual_dimension = dimension_reader()
        if actual_dimension != expected_dimension:
            raise ValueError(
                f"embedding model dimension {actual_dimension} does not match "
                f"configured dimension {expected_dimension}"
            )
        self._dimension = actual_dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vectors.tolist()


@lru_cache(maxsize=1)
def get_embedding_provider() -> SentenceTransformersProvider:
    settings = get_settings()
    return SentenceTransformersProvider(
        settings.embedding_model, settings.embedding_dimension
    )
