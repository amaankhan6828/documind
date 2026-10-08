"""Qdrant collection lifecycle and tenant-filtered dense search."""

from functools import lru_cache
from uuid import UUID

from qdrant_client import QdrantClient, models

from backend.app.core.config import Settings, get_settings
from backend.app.retrieval.embeddings import EmbeddingProvider, get_embedding_provider
from backend.app.retrieval.schemas import DenseCandidate


class QdrantIndexError(RuntimeError):
    """A Qdrant transport or write failure, safe to retry asynchronously."""


class CollectionConfigurationError(RuntimeError):
    """The existing collection does not match the configured vector dimensions."""


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=get_settings().qdrant_url, timeout=10)


def ensure_collection(
    client: QdrantClient,
    settings: Settings,
    *,
    dimension: int | None = None,
) -> None:
    """Create the configured cosine collection when absent; reject dimension drift."""
    vector_dimension = dimension or settings.embedding_dimension
    try:
        if not client.collection_exists(settings.qdrant_collection):
            client.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=models.VectorParams(
                    size=vector_dimension, distance=models.Distance.COSINE
                ),
            )
        else:
            collection = client.get_collection(settings.qdrant_collection)
            vector_config = collection.config.params.vectors
            if isinstance(vector_config, dict):
                raise CollectionConfigurationError(
                    "named-vector Qdrant collections are not supported by this index"
                )
            if vector_config.size != vector_dimension:
                raise CollectionConfigurationError(
                    f"collection dimension {vector_config.size} does not match "
                    f"configured dimension {vector_dimension}"
                )
        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name="tenant_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )
    except CollectionConfigurationError:
        raise
    except Exception as exc:
        raise QdrantIndexError("could not prepare Qdrant collection") from exc


def reset_collection(
    client: QdrantClient, settings: Settings, *, dimension: int | None = None
) -> None:
    """Delete and recreate the derived collection before a PostgreSQL rebuild."""
    try:
        if client.collection_exists(settings.qdrant_collection):
            client.delete_collection(settings.qdrant_collection)
    except Exception as exc:
        raise QdrantIndexError("could not remove old Qdrant collection") from exc
    ensure_collection(client, settings, dimension=dimension)


def search_dense(
    tenant_id: UUID,
    query_text: str,
    top_k: int,
    *,
    client: QdrantClient | None = None,
    embedder: EmbeddingProvider | None = None,
    settings: Settings | None = None,
) -> list[DenseCandidate]:
    """Return only this tenant's nearest chunk candidates, with provenance payload."""
    if not query_text.strip():
        raise ValueError("query text must not be blank")
    if top_k < 1 or top_k > 100:
        raise ValueError("top_k must be between 1 and 100")
    selected_settings = settings or get_settings()
    selected_client = client or get_qdrant_client()
    selected_embedder = embedder or get_embedding_provider()
    ensure_collection(selected_client, selected_settings, dimension=selected_embedder.dimension)
    vector = selected_embedder.embed([query_text])[0]
    if len(vector) != selected_embedder.dimension:
        raise ValueError("query embedding has an unexpected dimension")
    try:
        response = selected_client.query_points(
            collection_name=selected_settings.qdrant_collection,
            query=vector,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=str(tenant_id)),
                    )
                ]
            ),
            limit=top_k,
            with_payload=True,
        )
    except Exception as exc:
        raise QdrantIndexError("dense retrieval query failed") from exc

    candidates: list[DenseCandidate] = []
    for point in response.points:
        payload = point.payload or {}
        # Keep a second tenant check so a malformed/misconfigured index cannot leak hits.
        if payload.get("tenant_id") != str(tenant_id):
            continue
        candidates.append(
            DenseCandidate(chunk_id=UUID(str(point.id)), score=point.score, payload=payload)
        )
    return candidates
