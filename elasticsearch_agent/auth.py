"""JWT-based authentication and user store."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me-in-production-use-32-chars!!")
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "8"))

ROLE_ADMIN = "ROLE_ADMIN"
ROLE_USER = "ROLE_USER"


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class User:
    username: str
    password: str          # plain-text for POC — hash in production
    role: str
    seller_id: str | None  # None for ROLE_ADMIN (no row-level filter)


# ---------------------------------------------------------------------------
# User store — loaded from env or defaults
# ---------------------------------------------------------------------------
# Format: "username:password:ROLE:seller_id,username2:password2:ROLE_ADMIN"
# ROLE_ADMIN entries omit seller_id.

def _load_users() -> dict[str, User]:
    raw = os.getenv("USERS", "")
    if not raw.strip():
        raise ValueError(
            "No users configured. Set the USERS environment variable in .env.\n"
            "Format: username:password:ROLE_ADMIN,username2:password2:ROLE_USER:seller_id"
        )
    users: dict[str, User] = {}
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if len(parts) == 3:
            username, password, role = parts
            seller_id = None
        elif len(parts) == 4:
            username, password, role, seller_id = parts
        else:
            continue
        users[username] = User(
            username=username,
            password=password,
            role=role,
            seller_id=seller_id if role == ROLE_USER else None,
        )
    return users


_USERS: dict[str, User] = _load_users()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Raised for invalid credentials or expired/invalid tokens."""
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def login(username: str, password: str) -> str:
    """Validate credentials and return a signed JWT. Raises AuthError on failure."""
    user = _USERS.get(username)
    if not user or user.password != password:
        raise AuthError("Invalid username or password.")

    payload = {
        "sub": user.username,
        "role": user.role,
        "seller_id": user.seller_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def verify_token(token: str) -> dict:
    """Decode and validate a JWT. Returns the claims dict. Raises AuthError on failure."""
    try:
        claims = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return claims
    except jwt.ExpiredSignatureError:
        raise AuthError("Session expired. Please log in again.")
    except jwt.InvalidTokenError as e:
        raise AuthError(f"Invalid token: {e}")


def build_seller_filter(claims: dict) -> dict | None:
    """Return an ES filter clause for ROLE_USER, or None for ROLE_ADMIN."""
    if claims.get("role") == ROLE_USER and claims.get("seller_id"):
        return {"term": {"sellerId.keyword": claims["seller_id"]}}
    return None
