# Milestone 2A: Authentication

The API supports user registration, login, and the authenticated profile endpoint under `/api/v1/auth`. Registration requires an existing tenant ID; login uses email, password, and tenant ID because email uniqueness is tenant-scoped. New passwords are stored as Argon2id hashes. Login issues an HS256-signed JWT access token containing the user ID and tenant ID. The current-user dependency validates the token and loads the user from PostgreSQL before returning safe profile fields.

Required authentication configuration is `JWT_SECRET_KEY`; `JWT_ALGORITHM` defaults to `HS256`, and `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` defaults to 30. The example secret is development-only. Existing user rows receive a nullable password hash during migration and cannot authenticate until provisioned with a password through an approved account setup process.

See the root [README](../README.md) for local setup and an end-to-end registration/login example. RBAC, refresh tokens, invitations, password reset, email verification, OAuth, and document authorization are not included.
