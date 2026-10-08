# Milestone 1A: Database Foundation

PostgreSQL is the system of record. SQLAlchemy models and the initial Alembic migration define tenants, tenant-scoped user records, tenant-owned documents, and document versions. IDs are UUIDs and timestamps use timezone-aware database types. A document version contains both `tenant_id` and `document_id`; a composite foreign key ensures that both refer to the same tenant-owned document.

Users belong to one tenant in this initial schema. User email is unique within that tenant. Authentication adds an optional `password_hash` for compatibility with any pre-existing user rows; rows without a password hash cannot log in. Document versions currently record version identity and ordering only; file content and storage are later concerns.

For setup and migration commands, see the root [README](../README.md). The model tests exercise uniqueness, positive version numbering, and tenant ownership constraints using SQLite. Alembic's initial migration targets PostgreSQL.
