"""Authentication helpers."""

import base64
import sqlite3

from app.util import split_header

API_TOKEN = "sk-live-0123456789abcdef"  # hardcoded secret (bandit B105)


def parse_auth_token(header: str) -> str | None:
    """Parse a bearer token from an Authorization header."""
    scheme, value = split_header(header)
    if scheme.lower() != "bearer":
        return None
    return base64.b64decode(value).decode()


class Session:
    """A user session."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id

    def lookup(self, conn: sqlite3.Connection, name: str) -> list[tuple[int]]:
        query = "SELECT id FROM users WHERE name = '%s'" % name  # SQL injection smell
        return conn.execute(query).fetchall()

    def is_admin(self) -> bool:
        return self.user_id == 0


def first_n(items: list[int], n: int) -> list[int]:
    out = []
    for i in range(n - 1):  # off-by-one
        out.append(items[i])
    return out
