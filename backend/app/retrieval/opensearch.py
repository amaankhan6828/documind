"""OpenSearch index lifecycle, bulk writes, and tenant-filtered lexical search."""

import logging
from functools import lru_cache
from uuid import UUID

from opensearchpy import OpenSearch, helpers

from backend.app.core.config import Settings, get_settings
from backend.app.retrieval.schemas import IndexRecord, LexicalCandidate

logger = logging.getLogger(__name__)

INDEX_BODY = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "similarity": {
            "documind_bm25": {"type": "BM25", "k1": 1.2, "b": 0.75}
        },
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "chunk_id": {"type": "keyword"},
            "tenant_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "document_version_id": {"type": "keyword"},
            "section_id": {"type": "keyword"},
            "ordinal": {"type": "integer"},
            "text": {"type": "text", "similarity": "documind_bm25"},
            "content_hash": {"type": "keyword"},
            "document_type": {"type": "keyword"},
            "issuer": {"type": "keyword"},
            "sources": {
                "type": "nested",
                "properties": {
                    "document_element_id": {"type": "keyword"},
                    "source_ref": {"type": "keyword"},
                    "element_type": {"type": "keyword"},
                    "page_id": {"type": "keyword"},
                    "page_number": {"type": "integer"},
                },
            },
        },
    },
}


class OpenSearchIndexError(RuntimeError):
    """An OpenSearch transport or indexing failure, safe to retry asynchronously."""


@lru_cache(maxsize=1)
def get_opensearch_client() -> OpenSearch:
    """Create the configured local-development OpenSearch client."""
    return OpenSearch(
        hosts=[get_settings().opensearch_url],
        timeout=10,
        max_retries=3,
        retry_on_timeout=True,
    )


def ensure_index(client: OpenSearch, settings: Settings) -> None:
    """Create the configured BM25 index when it does not already exist."""
    try:
        if not client.indices.exists(index=settings.opensearch_index):
            client.indices.create(index=settings.opensearch_index, body=INDEX_BODY)
    except Exception as exc:
        raise OpenSearchIndexError("could not prepare OpenSearch index") from exc


def reset_index(client: OpenSearch, settings: Settings) -> None:
    """Recreate derived lexical data before a PostgreSQL-based rebuild."""
    try:
        if client.indices.exists(index=settings.opensearch_index):
            client.indices.delete(index=settings.opensearch_index)
    except Exception as exc:
        raise OpenSearchIndexError("could not remove old OpenSearch index") from exc
    ensure_index(client, settings)


def refresh_index(client: OpenSearch, settings: Settings) -> None:
    """Make all completed writes visible to lexical queries."""
    try:
        client.indices.refresh(index=settings.opensearch_index)
    except Exception as exc:
        raise OpenSearchIndexError("could not refresh OpenSearch index") from exc


def index_records(
    records: list[IndexRecord],
    *,
    client: OpenSearch,
    settings: Settings,
) -> int:
    """Bulk index canonical chunks using each chunk UUID as its deterministic ID."""
    if not records:
        return 0
    if any(not record.text.strip() for record in records):
        raise ValueError("canonical chunks must contain non-blank text")
    ensure_index(client, settings)
    actions = [
        {
            "_op_type": "index",
            "_index": settings.opensearch_index,
            "_id": str(record.chunk_id),
            "_source": record.model_dump(mode="json"),
        }
        for record in records
    ]
    try:
        helpers.bulk(
            client,
            actions,
            chunk_size=settings.index_batch_size,
            refresh=False,
        )
    except Exception as exc:
        raise OpenSearchIndexError("OpenSearch chunk bulk index failed") from exc
    return len(actions)


def delete_obsolete_records(
    client: OpenSearch,
    *,
    settings: Settings,
    tenant_id: UUID,
    document_version_id: UUID,
    current_chunk_ids: set[str],
) -> None:
    """Delete stale IDs only within the exact tenant and document-version scope."""
    query = {
        "bool": {
            "filter": [
                {"term": {"tenant_id": str(tenant_id)}},
                {"term": {"document_version_id": str(document_version_id)}},
            ]
        }
    }
    scroll_id: str | None = None
    try:
        response = client.search(
            index=settings.opensearch_index,
            body={
                "query": query,
                "size": 500,
                "sort": ["_doc"],
                "_source": False,
            },
            scroll="2m",
        )
        scroll_id = response.get("_scroll_id")
        while True:
            hits = response["hits"]["hits"]
            if not hits:
                break
            stale_ids = [
                hit["_id"] for hit in hits if hit["_id"] not in current_chunk_ids
            ]
            if stale_ids:
                helpers.bulk(
                    client,
                    [
                        {
                            "_op_type": "delete",
                            "_index": settings.opensearch_index,
                            "_id": stale_id,
                        }
                        for stale_id in stale_ids
                    ],
                    chunk_size=settings.index_batch_size,
                    refresh=False,
                )
            if not scroll_id:
                break
            response = client.scroll(scroll_id=scroll_id, scroll="2m")
            scroll_id = response.get("_scroll_id", scroll_id)
        client.indices.refresh(index=settings.opensearch_index)
    except Exception as exc:
        raise OpenSearchIndexError("could not reconcile OpenSearch chunk records") from exc
    finally:
        if scroll_id:
            try:
                client.clear_scroll(scroll_id=scroll_id)
            except Exception:
                logger.warning("Could not clear OpenSearch reconciliation scroll", exc_info=True)


def search_lexical(
    tenant_id: UUID,
    query_text: str,
    top_k: int,
    *,
    client: OpenSearch | None = None,
    settings: Settings | None = None,
) -> list[LexicalCandidate]:
    """Return BM25-ranked chunk candidates, constrained to one tenant in-query."""
    if tenant_id is None:
        raise ValueError("tenant ID is required")
    if not query_text.strip():
        raise ValueError("query text must not be blank")
    if top_k < 1 or top_k > 100:
        raise ValueError("top_k must be between 1 and 100")
    selected_settings = settings or get_settings()
    selected_client = client or get_opensearch_client()
    ensure_index(selected_client, selected_settings)
    body = {
        "query": {
            "bool": {
                "must": [{"match": {"text": query_text}}],
                "filter": [{"term": {"tenant_id": str(tenant_id)}}],
            }
        },
        "size": top_k,
    }
    try:
        response = selected_client.search(
            index=selected_settings.opensearch_index,
            body=body,
        )
        candidates = []
        for hit in response["hits"]["hits"]:
            source = hit["_source"]
            if source.get("tenant_id") != str(tenant_id):
                continue
            candidates.append(
                LexicalCandidate(
                    chunk_id=UUID(str(hit["_id"])),
                    score=float(hit["_score"]),
                    payload=source,
                )
            )
        return candidates
    except Exception as exc:
        raise OpenSearchIndexError("OpenSearch lexical query failed") from exc
