from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

logger = logging.getLogger("prospect.ui.security")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return base64.b64encode(salt + digest).decode("ascii")


def verify_password(password: str, encoded: str) -> bool:
    try:
        raw = base64.b64decode(encoded.encode("ascii"))
        salt, expected = raw[:16], raw[16:]
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def create_token(secret: str, username: str, expires_hours: int = 10) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=expires_hours)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> Optional[str]:
    """
    Decode and validate a JWT token.

    Returns the username (sub claim) on success, or None on any failure.
    Expired tokens are logged at DEBUG level — they are a normal occurrence
    when sessions time out and are NOT an error.
    Invalid/tampered tokens are logged at WARNING level.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        username = str(payload.get("sub") or "")
        if not username:
            logger.warning("JWT decoded successfully but 'sub' claim is empty.")
            return None
        return username
    except jwt.ExpiredSignatureError:
        logger.debug("JWT token has expired — user will be redirected to login.")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid JWT token rejected: %s", exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error decoding JWT token: %s", exc)
        return None
