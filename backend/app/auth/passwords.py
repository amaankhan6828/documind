"""Password hashing and verification using Argon2id."""

from pwdlib import PasswordHash

password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Return an Argon2id hash for a plaintext password."""
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Compare a candidate password with a stored hash."""
    return password_hasher.verify(password, password_hash)
