"""Unit tests for tenant-scoped reciprocal rank fusion."""

from uuid import UUID, uuid4

import pytest

from backend.app.retrieval import hybrid
from backend.app.retrieval.schemas import (
    DenseCandidate,
    HybridCandidate,
    LexicalCandidate,
)


def dense(
    chunk_id: UUID,
    score: float,
    payload: dict | None = None,
) -> DenseCandidate:
    return DenseCandidate(chunk_id=chunk_id, score=score, payload=payload or {})


def lexical(
    chunk_id: UUID,
    score: float,
    payload: dict | None = None,
) -> LexicalCandidate:
    return LexicalCandidate(chunk_id=chunk_id, score=score, payload=payload or {})


def test_rrf_uses_one_based_ranks_and_standard_formula() -> None:
    first, second = uuid4(), uuid4()

    results = hybrid.reciprocal_rank_fusion(
        [dense(first, 0.9), dense(second, 0.8)],
        [lexical(second, 4.2), lexical(first, 3.1)],
        top_k=10,
        rrf_k=2,
    )

    by_id = {result.chunk_id: result for result in results}
    assert by_id[first].score == pytest.approx(1 / 3 + 1 / 4)
    assert by_id[second].score == pytest.approx(1 / 4 + 1 / 3)
    assert by_id[first].dense_rank == 1
    assert by_id[first].lexical_rank == 2
    assert by_id[first].dense_score == 0.9
    assert by_id[first].lexical_score == 3.1


def test_rrf_preserves_metadata_merges_sources_and_adds_lexical_only_fields() -> None:
    chunk_id = uuid4()
    dense_source = {"source_ref": "#/text/1", "page_number": 1}
    lexical_source = {"source_ref": "#/text/2", "page_number": 2}
    shared_source = {"source_ref": "#/text/1", "page_number": 1}

    [result] = hybrid.reciprocal_rank_fusion(
        [
            dense(
                chunk_id,
                0.75,
                {
                    "tenant_id": "dense-tenant",
                    "document_id": "dense-document",
                    "sources": [dense_source],
                },
            )
        ],
        [
            lexical(
                chunk_id,
                2.5,
                {
                    "tenant_id": "lexical-tenant",
                    "document_id": "lexical-document",
                    "sources": [shared_source, lexical_source],
                    "text": "Canonical text",
                },
            )
        ],
        top_k=5,
    )

    assert result.chunk_id == chunk_id
    assert result.score == pytest.approx(1 / 61 + 1 / 61)
    assert result.payload == {
        "tenant_id": "dense-tenant",
        "document_id": "dense-document",
        "sources": [dense_source, lexical_source],
        "text": "Canonical text",
    }
    assert result.dense_rank == result.lexical_rank == 1
    assert result.dense_score == 0.75
    assert result.lexical_score == 2.5


def test_rrf_counts_repeated_dense_chunk_only_at_its_first_rank() -> None:
    repeated_id, other_id = uuid4(), uuid4()

    results = hybrid.reciprocal_rank_fusion(
        [
            dense(repeated_id, 0.9, {"source": "first"}),
            dense(other_id, 0.8),
            dense(repeated_id, 0.1, {"source": "duplicate"}),
        ],
        [],
        top_k=5,
        rrf_k=2,
    )
    result = next(candidate for candidate in results if candidate.chunk_id == repeated_id)

    assert len(results) == 2
    assert result.chunk_id == repeated_id
    assert result.score == pytest.approx(1 / 3)
    assert result.dense_rank == 1
    assert result.dense_score == 0.9
    assert result.payload == {"source": "first"}


def test_rrf_counts_repeated_lexical_chunk_only_at_its_first_rank() -> None:
    repeated_id, other_id = uuid4(), uuid4()

    results = hybrid.reciprocal_rank_fusion(
        [],
        [
            lexical(repeated_id, 4.2, {"text": "first"}),
            lexical(other_id, 3.1),
            lexical(repeated_id, 0.2, {"text": "duplicate"}),
        ],
        top_k=5,
        rrf_k=2,
    )
    result = next(candidate for candidate in results if candidate.chunk_id == repeated_id)

    assert len(results) == 2
    assert result.chunk_id == repeated_id
    assert result.score == pytest.approx(1 / 3)
    assert result.lexical_rank == 1
    assert result.lexical_score == 4.2
    assert result.payload == {"text": "first"}


def test_repeated_chunk_contributes_once_from_each_retriever() -> None:
    chunk_id = uuid4()

    [result] = hybrid.reciprocal_rank_fusion(
        [dense(chunk_id, 0.9), dense(chunk_id, 0.1)],
        [lexical(chunk_id, 4.2), lexical(chunk_id, 0.2)],
        top_k=5,
        rrf_k=2,
    )

    assert result.score == pytest.approx(1 / 3 + 1 / 3)
    assert result.dense_rank == result.lexical_rank == 1
    assert result.dense_score == 0.9
    assert result.lexical_score == 4.2


def test_rrf_orders_by_fused_score_then_chunk_uuid_for_ties() -> None:
    lower_id = UUID("00000000-0000-0000-0000-000000000001")
    higher_id = UUID("00000000-0000-0000-0000-000000000002")

    results = hybrid.reciprocal_rank_fusion(
        [dense(higher_id, 0.9)],
        [lexical(lower_id, 1.2)],
        top_k=5,
        rrf_k=60,
    )

    assert [result.chunk_id for result in results] == [lower_id, higher_id]


@pytest.mark.parametrize(
    ("dense_results", "lexical_results"),
    [
        ([], []),
        ([dense(uuid4(), 0.5)], []),
        ([], [lexical(uuid4(), 1.2)]),
    ],
)
def test_rrf_handles_empty_or_one_sided_inputs(
    dense_results: list[DenseCandidate],
    lexical_results: list[LexicalCandidate],
) -> None:
    results = hybrid.reciprocal_rank_fusion(
        dense_results, lexical_results, top_k=10
    )
    assert len(results) == len(dense_results) + len(lexical_results)


def test_rrf_applies_final_result_limit() -> None:
    candidates = [dense(uuid4(), 0.5) for _ in range(4)]

    results = hybrid.reciprocal_rank_fusion(candidates, [], top_k=2)

    assert len(results) == 2


def test_hybrid_search_passes_same_tenant_and_separate_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    dense_chunk, lexical_chunk = uuid4(), uuid4()
    dense_calls: list[tuple[UUID, str, int]] = []
    lexical_calls: list[tuple[UUID, str, int]] = []

    def fake_dense(candidate_tenant: UUID, query_text: str, limit: int):
        dense_calls.append((candidate_tenant, query_text, limit))
        return [dense(dense_chunk, 0.8)]

    def fake_lexical(candidate_tenant: UUID, query_text: str, limit: int):
        lexical_calls.append((candidate_tenant, query_text, limit))
        return [lexical(lexical_chunk, 1.5)]

    monkeypatch.setattr(hybrid, "search_dense", fake_dense)
    monkeypatch.setattr(hybrid, "search_lexical", fake_lexical)

    results = hybrid.search_hybrid(
        tenant_id,
        "revenue",
        dense_candidate_limit=7,
        lexical_candidate_limit=9,
        top_k=1,
    )

    assert dense_calls == [(tenant_id, "revenue", 7)]
    assert lexical_calls == [(tenant_id, "revenue", 9)]
    assert len(results) == 1


def test_hybrid_search_propagates_retrieval_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args):
        raise RuntimeError("dense retrieval unavailable")

    monkeypatch.setattr(hybrid, "search_dense", unavailable)
    monkeypatch.setattr(
        hybrid,
        "search_lexical",
        lambda *_args: pytest.fail("lexical retrieval should not run after dense error"),
    )

    with pytest.raises(RuntimeError, match="dense retrieval unavailable"):
        hybrid.search_hybrid(
            uuid4(),
            "revenue",
            dense_candidate_limit=5,
            lexical_candidate_limit=5,
            top_k=5,
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"dense_candidate_limit": 0, "lexical_candidate_limit": 5, "top_k": 5}, "dense_candidate_limit"),
        ({"dense_candidate_limit": 5, "lexical_candidate_limit": 101, "top_k": 5}, "lexical_candidate_limit"),
        ({"dense_candidate_limit": 5, "lexical_candidate_limit": 5, "top_k": 0}, "top_k"),
    ],
)
def test_hybrid_search_validates_candidate_and_final_limits(
    kwargs: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        hybrid.search_hybrid(uuid4(), "revenue", **kwargs)


def test_hybrid_search_requires_tenant_and_nonblank_query() -> None:
    common = {
        "dense_candidate_limit": 5,
        "lexical_candidate_limit": 5,
        "top_k": 5,
    }
    with pytest.raises(ValueError, match="tenant ID"):
        hybrid.search_hybrid(None, "revenue", **common)
    with pytest.raises(ValueError, match="blank"):
        hybrid.search_hybrid(uuid4(), " ", **common)


def test_hybrid_candidate_type_retains_optional_retriever_evidence() -> None:
    candidate = HybridCandidate(
        chunk_id=uuid4(),
        score=1 / 61,
        payload={"sources": []},
        dense_rank=1,
        dense_score=0.6,
    )

    assert candidate.lexical_rank is None
    assert candidate.lexical_score is None
