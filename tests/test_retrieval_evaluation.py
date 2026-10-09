"""Deterministic tests for retrieval evaluation metrics and orchestration."""

from math import log2
from uuid import UUID, uuid4

import pytest

from backend.app.retrieval.evaluation import (
    EvaluationQuery,
    RetrievalBenchmark,
    evaluate_benchmark,
    ndcg_at_k,
    reciprocal_rank,
    recall_at_k,
)
from backend.app.retrieval.schemas import DenseCandidate, HybridCandidate, LexicalCandidate


def test_recall_at_k_uses_all_known_relevant_ids_as_denominator() -> None:
    first_relevant, second_relevant, irrelevant = uuid4(), uuid4(), uuid4()

    assert recall_at_k(
        [irrelevant, first_relevant, second_relevant],
        {first_relevant, second_relevant, uuid4()},
        k=2,
    ) == pytest.approx(1 / 3)


def test_reciprocal_rank_uses_first_relevant_one_based_position() -> None:
    relevant, irrelevant = uuid4(), uuid4()

    assert reciprocal_rank([irrelevant, relevant, relevant], {relevant}) == pytest.approx(1 / 2)


def test_ndcg_at_k_uses_binary_gain_and_logarithmic_discount() -> None:
    first_relevant, second_relevant = uuid4(), uuid4()
    irrelevant = uuid4()
    ideal_dcg = 1 + 1 / log2(3)

    score = ndcg_at_k(
        [first_relevant, irrelevant, second_relevant],
        {first_relevant, second_relevant},
        k=3,
    )

    assert score == pytest.approx((1 + 1 / log2(4)) / ideal_dcg)


def test_metrics_count_each_ranked_chunk_once_at_its_first_position() -> None:
    irrelevant, first_relevant, second_relevant = uuid4(), uuid4(), uuid4()
    ranked = [irrelevant, first_relevant, first_relevant, second_relevant]
    relevant = {first_relevant, second_relevant}
    ideal_dcg = 1 + 1 / log2(3)

    assert recall_at_k(ranked, relevant, k=3) == 1.0
    assert reciprocal_rank(ranked, relevant) == pytest.approx(1 / 2)
    assert ndcg_at_k(ranked, relevant, k=3) == pytest.approx(
        (1 / log2(3) + 1 / log2(4)) / ideal_dcg
    )


def test_metrics_return_zero_when_judged_results_are_empty() -> None:
    relevant = {uuid4()}

    assert recall_at_k([], relevant, k=5) == 0.0
    assert reciprocal_rank([], relevant) == 0.0
    assert ndcg_at_k([], relevant, k=5) == 0.0


def test_metrics_return_none_when_query_has_no_relevance_labels() -> None:
    retrieved = [uuid4()]

    assert recall_at_k(retrieved, set(), k=5) is None
    assert reciprocal_rank(retrieved, set()) is None
    assert ndcg_at_k(retrieved, set(), k=5) is None


def test_ndcg_at_k_returns_zero_when_relevant_ids_rank_below_cutoff() -> None:
    relevant = uuid4()

    assert ndcg_at_k([uuid4(), uuid4(), relevant], {relevant}, k=2) == 0.0


def test_evaluation_runs_same_queries_for_all_methods_and_reports_unjudged(
) -> None:
    tenant_id = uuid4()
    relevant_id, other_id = uuid4(), uuid4()
    benchmark = RetrievalBenchmark(
        version=1,
        queries=[
            EvaluationQuery(query="judged query", relevant_chunk_ids=[relevant_id]),
            EvaluationQuery(query="unjudged query", relevant_chunk_ids=[]),
        ],
    )
    dense_calls: list[tuple[UUID, str, int]] = []
    lexical_calls: list[tuple[UUID, str, int]] = []
    hybrid_calls: list[tuple[UUID, str, dict]] = []

    def dense(tenant: UUID, query: str, limit: int) -> list[DenseCandidate]:
        dense_calls.append((tenant, query, limit))
        return [DenseCandidate(chunk_id=relevant_id, score=0.9, payload={})]

    def lexical(tenant: UUID, query: str, limit: int) -> list[LexicalCandidate]:
        lexical_calls.append((tenant, query, limit))
        return [LexicalCandidate(chunk_id=other_id, score=2.5, payload={})]

    def hybrid(
        tenant: UUID,
        query: str,
        **kwargs: int,
    ) -> list[HybridCandidate]:
        hybrid_calls.append((tenant, query, kwargs))
        return [
            HybridCandidate(
                chunk_id=relevant_id,
                score=0.03,
                payload={},
                dense_rank=1,
                dense_score=0.9,
                lexical_rank=2,
                lexical_score=2.5,
            )
        ]

    report = evaluate_benchmark(
        tenant_id,
        benchmark,
        k=2,
        candidate_limit=4,
        rrf_k=55,
        dense_retriever=dense,
        lexical_retriever=lexical,
        hybrid_retriever=hybrid,
    )

    assert dense_calls == [
        (tenant_id, "judged query", 4),
        (tenant_id, "unjudged query", 4),
    ]
    assert lexical_calls == dense_calls
    assert hybrid_calls == [
        (
            tenant_id,
            "judged query",
            {
                "dense_candidate_limit": 4,
                "lexical_candidate_limit": 4,
                "top_k": 4,
                "rrf_k": 55,
            },
        ),
        (
            tenant_id,
            "unjudged query",
            {
                "dense_candidate_limit": 4,
                "lexical_candidate_limit": 4,
                "top_k": 4,
                "rrf_k": 55,
            },
        ),
    ]
    assert report["tenant_id"] == str(tenant_id)
    assert report["methods"]["dense"]["aggregate"] == {
        "query_count": 2,
        "judged_query_count": 1,
        "unjudged_query_count": 1,
        "recall_at_k": 1.0,
        "mrr": 1.0,
        "ndcg_at_k": 1.0,
    }
    assert report["methods"]["lexical"]["aggregate"]["recall_at_k"] == 0.0
    assert report["methods"]["hybrid"]["aggregate"]["mrr"] == 1.0
    assert report["methods"]["dense"]["per_query"][1]["recall_at_k"] is None


def test_evaluation_report_deduplicates_ranked_chunk_ids_in_first_occurrence_order() -> None:
    tenant_id = uuid4()
    first_id, second_id = uuid4(), uuid4()
    benchmark = RetrievalBenchmark(
        version=1,
        queries=[EvaluationQuery(query="query", relevant_chunk_ids=[first_id])],
    )

    report = evaluate_benchmark(
        tenant_id,
        benchmark,
        k=2,
        candidate_limit=3,
        dense_retriever=lambda *_: [
            DenseCandidate(chunk_id=first_id, score=0.9, payload={}),
            DenseCandidate(chunk_id=first_id, score=0.8, payload={}),
            DenseCandidate(chunk_id=second_id, score=0.7, payload={}),
        ],
        lexical_retriever=lambda *_: [
            LexicalCandidate(chunk_id=second_id, score=2.5, payload={}),
            LexicalCandidate(chunk_id=first_id, score=2.0, payload={}),
            LexicalCandidate(chunk_id=second_id, score=1.5, payload={}),
        ],
        hybrid_retriever=lambda *_args, **_kwargs: [
            HybridCandidate(
                chunk_id=second_id,
                score=0.03,
                payload={},
                dense_rank=1,
                dense_score=0.9,
                lexical_rank=2,
                lexical_score=2.5,
            ),
            HybridCandidate(
                chunk_id=second_id,
                score=0.02,
                payload={},
                dense_rank=2,
                dense_score=0.8,
                lexical_rank=3,
                lexical_score=1.5,
            ),
            HybridCandidate(
                chunk_id=first_id,
                score=0.01,
                payload={},
                dense_rank=3,
                dense_score=0.7,
                lexical_rank=1,
                lexical_score=3.0,
            ),
        ],
    )

    assert report["methods"]["dense"]["per_query"][0]["ranked_chunk_ids"] == [
        str(first_id),
        str(second_id),
    ]
    assert report["methods"]["lexical"]["per_query"][0]["ranked_chunk_ids"] == [
        str(second_id),
        str(first_id),
    ]
    assert report["methods"]["hybrid"]["per_query"][0]["ranked_chunk_ids"] == [
        str(second_id),
        str(first_id),
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"k": 0}, "k"),
        ({"k": 3, "candidate_limit": 2}, "greater than or equal"),
        ({"candidate_limit": 101}, "candidate_limit"),
        ({"rrf_k": 0}, "rrf_k"),
    ],
)
def test_evaluation_validates_limits(kwargs: dict[str, int], message: str) -> None:
    benchmark = RetrievalBenchmark(version=1, queries=[])

    with pytest.raises(ValueError, match=message):
        evaluate_benchmark(uuid4(), benchmark, **kwargs)


def test_benchmark_rejects_extra_fields_and_blank_queries() -> None:
    with pytest.raises(ValueError):
        EvaluationQuery(query="query", relevant_chunk_ids=[], tenant_id=str(uuid4()))
    with pytest.raises(ValueError):
        EvaluationQuery(query="  ", relevant_chunk_ids=[])


def test_benchmark_rejects_duplicate_relevant_chunk_ids() -> None:
    duplicate_id = uuid4()

    with pytest.raises(
        ValueError,
        match="relevant_chunk_ids must not contain duplicate chunk IDs",
    ):
        EvaluationQuery(
            query="query",
            relevant_chunk_ids=[duplicate_id, duplicate_id],
        )
