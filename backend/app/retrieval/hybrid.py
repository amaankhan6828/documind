"""Tenant-scoped dense and lexical retrieval combined with reciprocal rank fusion."""

from uuid import UUID

from backend.app.retrieval.opensearch import search_lexical
from backend.app.retrieval.qdrant import search_dense
from backend.app.retrieval.schemas import DenseCandidate, HybridCandidate, LexicalCandidate


def _merge_payloads(existing: dict, incoming: dict) -> dict:
    """Preserve existing metadata, add absent fields, and merge source references."""
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "sources" and isinstance(merged.get(key), list) and isinstance(value, list):
            sources = list(merged[key])
            sources.extend(source for source in value if source not in sources)
            merged[key] = sources
        else:
            merged.setdefault(key, value)
    return merged


def reciprocal_rank_fusion(
    dense_results: list[DenseCandidate],
    lexical_results: list[LexicalCandidate],
    *,
    top_k: int,
    rrf_k: int = 60,
) -> list[HybridCandidate]:
    """Fuse ranked results with 1 / (rrf_k + one-based rank) contributions."""
    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be between 1 and 100")
    if rrf_k < 1:
        raise ValueError("rrf_k must be at least 1")

    fused: dict[UUID, HybridCandidate] = {}
    seen_dense: set[UUID] = set()
    for rank, candidate in enumerate(dense_results, start=1):
        if candidate.chunk_id in seen_dense:
            continue
        seen_dense.add(candidate.chunk_id)
        fused[candidate.chunk_id] = HybridCandidate(
            chunk_id=candidate.chunk_id,
            score=1 / (rrf_k + rank),
            payload=dict(candidate.payload),
            dense_rank=rank,
            dense_score=candidate.score,
        )

    seen_lexical: set[UUID] = set()
    for rank, candidate in enumerate(lexical_results, start=1):
        if candidate.chunk_id in seen_lexical:
            continue
        seen_lexical.add(candidate.chunk_id)
        existing = fused.get(candidate.chunk_id)
        if existing is None:
            fused[candidate.chunk_id] = HybridCandidate(
                chunk_id=candidate.chunk_id,
                score=1 / (rrf_k + rank),
                payload=dict(candidate.payload),
                lexical_rank=rank,
                lexical_score=candidate.score,
            )
            continue

        existing.score += 1 / (rrf_k + rank)
        existing.payload = _merge_payloads(existing.payload, candidate.payload)
        existing.lexical_rank = rank
        existing.lexical_score = candidate.score

    return sorted(
        fused.values(),
        key=lambda candidate: (-candidate.score, str(candidate.chunk_id)),
    )[:top_k]


def search_hybrid(
    tenant_id: UUID,
    query_text: str,
    *,
    dense_candidate_limit: int,
    lexical_candidate_limit: int,
    top_k: int,
    rrf_k: int = 60,
) -> list[HybridCandidate]:
    """Retrieve candidates for one tenant and combine them with reciprocal rank fusion."""
    if tenant_id is None:
        raise ValueError("tenant ID is required")
    if not query_text.strip():
        raise ValueError("query text must not be blank")
    if not 1 <= dense_candidate_limit <= 100:
        raise ValueError("dense_candidate_limit must be between 1 and 100")
    if not 1 <= lexical_candidate_limit <= 100:
        raise ValueError("lexical_candidate_limit must be between 1 and 100")

    dense_results = search_dense(tenant_id, query_text, dense_candidate_limit)
    lexical_results = search_lexical(tenant_id, query_text, lexical_candidate_limit)
    return reciprocal_rank_fusion(
        dense_results,
        lexical_results,
        top_k=top_k,
        rrf_k=rrf_k,
    )
