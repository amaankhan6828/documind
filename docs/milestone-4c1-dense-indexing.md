# Milestone 4C.1 — Qdrant Dense Indexing

## Purpose and source of truth

PostgreSQL canonical chunks remain authoritative. The worker asynchronously embeds committed chunks and upserts deterministic points into Qdrant. A Qdrant failure does not roll back chunk persistence or change a completed canonical processing job. The separate indexing task retries Qdrant transport/write failures up to three times; if task dispatch itself is unavailable, the failure is logged and the index can be rebuilt.

## Local services and configuration

Compose adds Qdrant `qdrant/qdrant:v1.19.1` with persistent named volume `qdrant_data`. The host REST port is bound to loopback. The worker connects to `http://qdrant:6333`; host processes use `http://localhost:6333`.

Configuration is environment-driven:

- `QDRANT_URL`: service URL.
- `QDRANT_COLLECTION`: collection name (default `documind_chunks`).
- `EMBEDDING_MODEL`: local Sentence Transformers model, default `sentence-transformers/all-MiniLM-L6-v2`.
- `EMBEDDING_DIMENSION`: expected vector size, default `384`.
- `INDEX_BATCH_SIZE`: PostgreSQL and Qdrant batch size, default `64`.

The model executes locally through Sentence Transformers on CPU. Its model files must be available in the Hugging Face cache or fetched when first initialized. The adapter validates model output dimension against configuration. Vectors are normalized and indexed with cosine distance.

## Projection and tenant filtering

Each point ID is the canonical PostgreSQL chunk UUID, so upsert is idempotent. Compact payloads include tenant, document, version, section when known, chunk ID/order, source element references and types, page references where Docling provided them, document type/issuer when available, and the canonical chunk content hash. Chunk text remains in PostgreSQL and can be hydrated by returned chunk ID.

The internal `search_dense(tenant_id, query_text, top_k)` service embeds the query and applies a Qdrant `tenant_id` filter. It also discards any returned point whose payload tenant does not match, as a second isolation check. This milestone adds no public query endpoint.

## Rebuilding derived state

Run from the repository root after configuring PostgreSQL and Qdrant:

```bash
python -m backend.app.retrieval.rebuild
```

The command deletes and recreates the configured Qdrant collection, reads canonical chunks from PostgreSQL in bounded batches, re-embeds them, and upserts the same chunk IDs. Qdrant can therefore be discarded and reconstructed without losing canonical document data. Do not run this command concurrently with active indexing unless a temporary empty/rebuilding index is acceptable.

## Scope limits

Only dense indexing and internal dense retrieval are included. BM25/OpenSearch, hybrid fusion, reranking, graph retrieval, LLM answering, and RAG remain out of scope. Local Qdrant has no API key configured and is intended only for development; keep it network-restricted outside this local setup.
