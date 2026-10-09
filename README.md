# DocuMind

DocuMind is an enterprise multimodal document intelligence and reasoning platform. It is intended to help users retrieve and reason over documents with traceable, page-level evidence.

## Current scope: Milestone 4C.1

The foundation includes FastAPI, a minimal Next.js/TypeScript shell, PostgreSQL, and SQLAlchemy models for tenants, tenant-scoped users, documents, and document versions.

Milestone 2A added Argon2id password hashing and JWT bearer authentication. Milestone 2B added authenticated tenant-scoped document metadata CRUD. Milestone 2C added PDF/DOCX upload, local file storage, SHA-256 checksums, and document version records. Milestone 3 added PostgreSQL-backed processing jobs, Redis task transport, a Celery worker, and tenant-scoped job status. Milestone 4A uses Docling in the worker to convert uploaded PDF/DOCX versions into canonical PostgreSQL pages, sections, text, table, and figure records with parser-provided provenance.

Milestone 4B derives canonical structural chunks from those records and stores them in PostgreSQL with source-element and page provenance. Milestone 4C.1 adds a rebuildable Qdrant dense index over those chunks using local Sentence Transformers embeddings. Milestone 4C.2 adds a rebuildable OpenSearch BM25 lexical index over the same canonical chunks. Milestone 4C.3 combines dense and lexical candidates using tenant-scoped reciprocal rank fusion. PostgreSQL remains canonical for both derived indexes.

Authentication does not include RBAC, tenant administration, refresh tokens, OAuth, password reset, or email verification. Files are stored locally. Reranking, knowledge graphs, LLM answers, and RAG are not implemented.

## Prerequisites

- Python 3.11 or newer
- Node.js 20.9 or newer and npm
- Docker Compose

## Local setup

### Backend and PostgreSQL

Copy the local environment template and start PostgreSQL:

```bash
cp .env.example .env
docker compose up -d db
```

From the repository root, create and activate a virtual environment, then install the backend and test requirements:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt
alembic -c backend/alembic.ini upgrade head
uvicorn backend.app.main:app --reload
```

Start Redis as well for upload-triggered processing jobs (`docker compose up -d redis`). When running FastAPI directly on the host, set `REDIS_URL=redis://localhost:6379/0`. In a separate terminal, run the worker with the same environment and database settings:

```bash
celery -A backend.app.workers.celery_app:celery_app worker --loglevel=info
```

Settings are read from environment variables and the root `.env` file. The example PostgreSQL password is for local development only; replace it for any shared environment. The API is available at `http://localhost:8000`; check `http://localhost:8000/health` for `{"status":"ok"}`. Health is a liveness check and does not require a database connection.

Authentication uses `JWT_SECRET_KEY`, `JWT_ALGORITHM` (currently `HS256`), and `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`. The example key in `.env.example` is public and for local development only. Replace it with a unique secret, for example `openssl rand -hex 32`, before using a shared or deployed environment. Passwords must contain at least 12 characters.

Registration requires an existing `tenant_id`; tenant creation and invitation management are not part of this milestone. Create or identify a local tenant in PostgreSQL, then register and log in through `/docs` or the API:

```bash
docker compose exec db psql -U documind -d documind -c \
  "INSERT INTO tenants (id, name, slug) VALUES (gen_random_uuid(), 'Local Development', 'local-dev') ON CONFLICT (slug) DO NOTHING"
docker compose exec db psql -U documind -d documind -c \
  "SELECT id FROM tenants WHERE slug = 'local-dev'"
```

Pass that tenant UUID with the email and password to `POST /api/v1/auth/register` and `POST /api/v1/auth/login`. Login also accepts `tenant_id` because email addresses are unique within a tenant. Send the returned token as `Authorization: Bearer <access_token>` to `GET /api/v1/auth/me`. The current-user response includes the tenant ID and excludes the password hash.

Run the full backend, authentication, tenant-isolation, model/constraint, and health test suite from the repository root. Tests use the existing in-memory SQLite strategy and do not require PostgreSQL:

```bash
python -m pytest
```

### Tenant-scoped document API

All endpoints below require `Authorization: Bearer <access_token>` from login:

- `POST /api/v1/documents` creates a logical document. Supply `title`, `document_type`, and optional `issuer`/`description`; tenant ownership is assigned from the token's authenticated user.
- `GET /api/v1/documents?limit=50&offset=0` lists only the caller's tenant documents.
- `GET /api/v1/documents/{document_id}` retrieves a tenant-owned document; other tenants' IDs return the same `404` as missing documents.
- `PATCH /api/v1/documents/{document_id}` updates only title, document type, issuer, or description.
- `DELETE /api/v1/documents/{document_id}` deletes the logical record and its related versions; it does not delete files.

The document API manages metadata and supports PDF/DOCX version uploads at `POST /api/v1/documents/{document_id}/versions` using multipart field `file`. Uploads require authentication and a document in the authenticated user's tenant. Configure `DOCUMENT_STORAGE_PATH` (default `./storage/documents`) and `MAX_UPLOAD_SIZE_MB` (default `20`) in `.env`; stored files are excluded from Git. Uploads are hashed and duplicate content on the same document returns `409 Conflict`. Each successful upload also creates and queues a `DOCUMENT_PROCESSING` job. Query `GET /api/v1/jobs/{job_id}` for tenant-scoped status. See [Milestone 2B notes](docs/milestone-2b-document-api.md), [Milestone 2C notes](docs/milestone-2c-file-upload.md), and [Milestone 3 notes](docs/milestone-3-async-processing.md).

### Document intelligence

Milestone 4A replaces the placeholder worker with a Docling adapter restricted to the stored PDF/DOCX file. The worker persists parser-reported pages, section headings, text, tables, and figure references in PostgreSQL. Extracted elements retain source references and available page, bounding-box, and character-span provenance; missing page information stays missing. Selectable-text PDFs use Docling's model-free native pipeline; image-only PDFs use Docling's standard pipeline with local Tesseract OCR. Milestone 4B groups canonical elements into deterministic chunks under configurable `CHUNK_MAX_CHARACTERS` and `CHUNK_OVERLAP_CHARACTERS` limits. Tables stay intact as standalone chunks. Milestones 4C.1 and 4C.2 asynchronously project these chunks into Qdrant for dense retrieval and OpenSearch for tenant-filtered BM25 lexical retrieval; all canonical text remains in PostgreSQL. Run `python -m backend.app.retrieval.rebuild_opensearch` to recreate the lexical index from PostgreSQL. See [Milestone 4A notes](docs/milestone-4a-document-intelligence.md), [Milestone 4B notes](docs/milestone-4b-chunking.md), [Milestone 4C.1 notes](docs/milestone-4c1-dense-indexing.md), and [Milestone 4C.2 notes](docs/milestone-4c2-lexical-indexing.md).

### Frontend

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

### Full local stack with Docker Compose

After copying `.env.example` to `.env`, start PostgreSQL, Redis, Qdrant, OpenSearch, the API, Celery worker, and the frontend:

```bash
docker compose up --build
```

Apply the schema with the Alembic command above before using database-backed features. Compose starts Redis as the Celery broker, Qdrant as the derived dense index, and a local security-disabled OpenSearch service as the derived BM25 index. OpenSearch is bound to localhost and uses no credentials only for local development; do not expose this configuration in shared or production environments. PostgreSQL remains the source of truth for jobs, document structure, and chunks. See `docs/milestone-4c1-dense-indexing.md` for Qdrant setup and `docs/milestone-4c2-lexical-indexing.md` for OpenSearch configuration, tenant-filtered BM25 search, and rebuilding.
