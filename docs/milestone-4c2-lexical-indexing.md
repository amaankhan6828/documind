# Milestone 4C.2: OpenSearch BM25 Lexical Index

OpenSearch is a rebuildable lexical retrieval projection of canonical PostgreSQL document chunks. It indexes chunk text, identifiers, document metadata, content hashes, and parser-reported source/page references. Chunk UUIDs are deterministic OpenSearch document IDs. PostgreSQL remains the source of truth.

The worker queues OpenSearch indexing independently from Qdrant after canonical chunk commits. OpenSearch transport/write failures are retried by their own Celery task; they do not roll back canonical processing or change Qdrant indexing. Version reconciliation indexes all current chunks successfully before deleting obsolete records, constrained by both tenant and document-version IDs.

## Local development

Set `OPENSEARCH_URL` and `OPENSEARCH_INDEX` in `.env`. Compose runs OpenSearch `2.19.1` as a single-node, security-disabled local service, bound to `127.0.0.1:${OPENSEARCH_PORT:-9200}`. This configuration is for local development only.

Start OpenSearch with `docker compose up -d opensearch`. Recreate the derived index from canonical database chunks with:

```bash
python -m backend.app.retrieval.rebuild_opensearch
```

The retrieval adapter exposes `search_lexical(tenant_id, query_text, top_k)`. It requires a tenant UUID and places that tenant filter inside the OpenSearch query. BM25 score order is returned with canonical text and available provenance in each candidate's `payload`.

No database migration is required; this milestone adds no PostgreSQL tables or columns.

## Hybrid retrieval

The internal `search_hybrid(tenant_id, query_text, ...)` combines the existing dense and lexical candidate lists using reciprocal rank fusion (RRF), without mixing their incomparable raw scores. Each one-based rank contributes `1 / (rrf_k + rank)`; `rrf_k` defaults to 60. A canonical chunk found by both retrievers appears once with both contributions. Results retain available payload provenance and each retriever's rank and score, and equal fused scores are ordered by chunk UUID. Dense and lexical candidate limits are independent of the final result limit. Tenant identity is supplied explicitly to both adapters, not taken from search filters.

The authenticated `POST /api/v1/search/hybrid` endpoint obtains tenant identity from the bearer-authenticated user, rejects extra request fields (including `tenant_id`), and returns typed fused candidates. Qdrant or OpenSearch service errors produce `503 Service Unavailable`.
