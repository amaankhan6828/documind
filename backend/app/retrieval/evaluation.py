"""Metrics and tenant-scoped evaluation for dense, lexical, and hybrid retrieval."""

from collections.abc import Callable, Iterable, Sequence
from math import log2
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.retrieval.hybrid import search_hybrid
from backend.app.retrieval.opensearch import search_lexical
from backend.app.retrieval.qdrant import search_dense
from backend.app.retrieval.schemas import (
    DenseCandidate,
    HybridCandidate,
    LexicalCandidate,
)


class EvaluationQuery(BaseModel):
    """A benchmark query with independently assigned relevant chunk IDs."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=10_000)
    relevant_chunk_ids: list[UUID]

    @field_validator("query")
    @classmethod
    def require_nonblank_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("benchmark query must not be blank")
        return value

    @field_validator("relevant_chunk_ids")
    @classmethod
    def require_unique_relevant_chunk_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("relevant_chunk_ids must not contain duplicate chunk IDs")
        return value


class RetrievalBenchmark(BaseModel):
    """Versioned benchmark records maintained separately from application data."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    queries: list[EvaluationQuery]


def recall_at_k(
    ranked_chunk_ids: Sequence[UUID],
    relevant_chunk_ids: set[UUID],
    *,
    k: int,
) -> float | None:
    """Return relevant IDs in the first k divided by all known relevant IDs.

    Returns None when no relevance labels exist for the query.
    """
    _validate_k(k)
    if not relevant_chunk_ids:
        return None
    retrieved = set(_unique_in_order(ranked_chunk_ids)[:k])
    return len(retrieved & relevant_chunk_ids) / len(relevant_chunk_ids)


def reciprocal_rank(
    ranked_chunk_ids: Sequence[UUID],
    relevant_chunk_ids: set[UUID],
) -> float | None:
    """Return the reciprocal rank of the first relevant result, or zero if absent.

    Returns None when no relevance labels exist for the query.
    """
    if not relevant_chunk_ids:
        return None
    for rank, chunk_id in enumerate(_unique_in_order(ranked_chunk_ids), start=1):
        if chunk_id in relevant_chunk_ids:
            return 1 / rank
    return 0.0


def ndcg_at_k(
    ranked_chunk_ids: Sequence[UUID],
    relevant_chunk_ids: set[UUID],
    *,
    k: int,
) -> float | None:
    """Compute binary-relevance nDCG@k using log2(rank + 1) discounts.

    Returns None when no relevance labels exist for the query.
    """
    _validate_k(k)
    if not relevant_chunk_ids:
        return None
    dcg = sum(
        1 / log2(rank + 1)
        for rank, chunk_id in enumerate(_unique_in_order(ranked_chunk_ids)[:k], start=1)
        if chunk_id in relevant_chunk_ids
    )
    ideal_relevant_count = min(k, len(relevant_chunk_ids))
    ideal_dcg = sum(1 / log2(rank + 1) for rank in range(1, ideal_relevant_count + 1))
    return dcg / ideal_dcg


def evaluate_benchmark(
    tenant_id: UUID,
    benchmark: RetrievalBenchmark,
    *,
    k: int = 10,
    candidate_limit: int = 20,
    rrf_k: int = 60,
    dense_retriever: Callable[[UUID, str, int], list[DenseCandidate]] | None = None,
    lexical_retriever: Callable[[UUID, str, int], list[LexicalCandidate]] | None = None,
    hybrid_retriever: Callable[..., list[HybridCandidate]] | None = None,
) -> dict:
    """Evaluate all retrieval methods for the same judged queries and tenant."""
    _validate_k(k)
    if not 1 <= candidate_limit <= 100:
        raise ValueError("candidate_limit must be between 1 and 100")
    if candidate_limit < k:
        raise ValueError("candidate_limit must be greater than or equal to k")
    if rrf_k < 1:
        raise ValueError("rrf_k must be at least 1")
    if tenant_id is None:
        raise ValueError("tenant ID is required")

    selected_dense_retriever = dense_retriever or search_dense
    selected_lexical_retriever = lexical_retriever or search_lexical
    selected_hybrid_retriever = hybrid_retriever or search_hybrid
    methods = ("dense", "lexical", "hybrid")
    per_method: dict[str, list[dict]] = {method: [] for method in methods}

    for benchmark_query in benchmark.queries:
        relevant = set(benchmark_query.relevant_chunk_ids)
        dense_results = selected_dense_retriever(
            tenant_id, benchmark_query.query, candidate_limit
        )
        lexical_results = selected_lexical_retriever(
            tenant_id, benchmark_query.query, candidate_limit
        )
        hybrid_results = selected_hybrid_retriever(
            tenant_id,
            benchmark_query.query,
            dense_candidate_limit=candidate_limit,
            lexical_candidate_limit=candidate_limit,
            top_k=candidate_limit,
            rrf_k=rrf_k,
        )
        ranked_by_method = {
            "dense": _unique_in_order(
                [candidate.chunk_id for candidate in dense_results]
            ),
            "lexical": _unique_in_order(
                [candidate.chunk_id for candidate in lexical_results]
            ),
            "hybrid": _unique_in_order(
                [candidate.chunk_id for candidate in hybrid_results]
            ),
        }
        for method, ranked_chunk_ids in ranked_by_method.items():
            per_method[method].append(
                {
                    "query": benchmark_query.query,
                    "relevant_chunk_ids": [
                        str(chunk_id) for chunk_id in benchmark_query.relevant_chunk_ids
                    ],
                    "ranked_chunk_ids": [str(chunk_id) for chunk_id in ranked_chunk_ids],
                    "recall_at_k": recall_at_k(ranked_chunk_ids, relevant, k=k),
                    "mrr": reciprocal_rank(ranked_chunk_ids, relevant),
                    "ndcg_at_k": ndcg_at_k(ranked_chunk_ids, relevant, k=k),
                }
            )

    judged_query_count = sum(bool(query.relevant_chunk_ids) for query in benchmark.queries)
    method_reports = {}
    for method, query_results in per_method.items():
        method_reports[method] = {
            "aggregate": {
                "query_count": len(benchmark.queries),
                "judged_query_count": judged_query_count,
                "unjudged_query_count": len(benchmark.queries) - judged_query_count,
                "recall_at_k": _mean(query["recall_at_k"] for query in query_results),
                "mrr": _mean(query["mrr"] for query in query_results),
                "ndcg_at_k": _mean(query["ndcg_at_k"] for query in query_results),
            },
            "per_query": query_results,
        }
    return {"tenant_id": str(tenant_id), "k": k, "methods": method_reports}


def _mean(values: Iterable[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    if not available:
        return None
    return sum(available) / len(available)


def _unique_in_order(chunk_ids: Sequence[UUID]) -> list[UUID]:
    return list(dict.fromkeys(chunk_ids))


def _validate_k(k: int) -> None:
    if k < 1:
        raise ValueError("k must be at least 1")
