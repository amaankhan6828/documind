# Retrieval Evaluation Baseline

`scripts/evaluate_retrieval.py` compares dense Qdrant retrieval, lexical OpenSearch retrieval, and hybrid RRF for the same benchmark queries and one explicitly selected tenant. It uses the existing retrieval adapters and does not alter their ranking or tenant filters.

## Prepare independent relevance labels

Copy `eval/retrieval/benchmark.example.json` to a private/local benchmark file. For each query, add only chunk UUIDs that have been independently reviewed as relevant:

```json
{
  "version": 1,
  "queries": [
    {
      "query": "What was the reported quarterly revenue?",
      "relevant_chunk_ids": ["reviewed-canonical-chunk-uuid"]
    }
  ]
}
```

Find candidate canonical chunks for review from PostgreSQL, for example with a tenant-scoped query over `document_chunks` and `documents`, and judge their text and provenance manually. Do not copy retrieved top results into the relevance labels without independent review. Keep actual tenant IDs and document content out of committed example data. A query with an empty `relevant_chunk_ids` list is unjudged; it is reported per query but excluded from metric averages.

## Run locally

Ensure PostgreSQL is migrated and Qdrant/OpenSearch contain indexed chunks for the selected tenant. Then run from the repository root:

```bash
.venv/bin/python -m scripts.evaluate_retrieval \
  --tenant-id <tenant-uuid> \
  --benchmark /path/to/retrieval-benchmark.json \
  --k 10 \
  --candidate-limit 20 \
  --rrf-k 60
```

The tenant ID is a required CLI argument and is passed unchanged to all three retrieval functions. The report is JSON on stdout with aggregate and per-query metrics and ranked canonical chunk IDs. No relevance labels means the aggregate metrics are `null`; no meaningful quality comparison is possible until queries have independently reviewed labels.

## Metric definitions

- **Recall@K:** distinct judged-relevant chunk IDs in the first K results divided by all judged-relevant chunk IDs for that query.
- **MRR:** reciprocal of the one-based rank of the first relevant result in the returned candidate list; zero if none was returned. The candidate limit bounds the list used for MRR.
- **nDCG@K:** binary relevance discounted by `log2(rank + 1)`, divided by ideal DCG for up to `min(K, number of relevant IDs)` relevant results.

Ranked results are normalized by canonical chunk UUID before metric calculation: repeated UUIDs count once, at their first occurrence. Duplicate UUIDs in relevance labels are rejected.

For a query with relevance labels but no retrieved results, all three metrics are zero. A query with no relevance labels has `null` metrics and is excluded from macro-average aggregates.
