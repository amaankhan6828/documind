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
