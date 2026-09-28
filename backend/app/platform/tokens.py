"""Bearer tokens that are stored only as a hash: sessions, invites, capability links.

The token itself goes to its holder once; the database keeps its SHA-256, so a leaked table grants
nothing. A plain hash is enough (no salt, no slow hash) because the token is 256 random bits, not a
password someone chose.
"""

import hashlib
import secrets


def new_token() -> tuple[str, bytes]:
    """A URL-safe token and the digest to store."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()
