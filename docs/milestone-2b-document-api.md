# Milestone 2B: Tenant-Scoped Document API

DocuMind now supports authenticated CRUD for logical document metadata. The API uses the existing JWT and SQLAlchemy session dependencies. Every create operation assigns the authenticated user's `tenant_id`; list, retrieve, update, and delete queries include that tenant ID. A document outside the caller's tenant is returned as `404` so its existence is not disclosed.

## Endpoints

All routes require a bearer access token:

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/documents` | Create a logical document |
| `GET` | `/api/v1/documents` | List tenant documents (`limit`, `offset`) |
| `GET` | `/api/v1/documents/{document_id}` | Retrieve one tenant-owned document |
| `PATCH` | `/api/v1/documents/{document_id}` | Update allowed metadata |
| `DELETE` | `/api/v1/documents/{document_id}` | Delete the logical document and related versions |

Create requires `title` and `document_type`; `issuer` and `description` are optional. Updates accept only those four metadata fields. The API does not accept tenant or ownership fields.

## Scope and limitations

This milestone stores document metadata only. File upload, object storage, file deletion, document processing, search, and AI features are not implemented. The nullable metadata columns preserve compatibility with existing document rows. Apply the new Alembic migration with `alembic -c backend/alembic.ini upgrade head` before using the API against PostgreSQL.

Run document and authentication tests from the repository root with `python -m pytest -q`.
