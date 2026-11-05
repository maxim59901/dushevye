"""Password hashing, roles, CSRF tokens, and login rate limiting."""
from __future__ import annotations

import secrets
import string
import threading
import time
from typing import Optional

import bcrypt

ROLES = (
    "admin",
    "owner",
    "accountant",
    "manager",
    "measurer",
    "installer",
    "supply",
    "production",
)

ROLE_LABELS_RU = {
    "admin": "Админ",
    "owner": "Хозяин",
    "accountant": "Бухгалтер",
    "manager": "Менеджер",
    "measurer": "Замерщик",
    "installer": "Монтажник",
    "supply": "Снабжение",
    "production": "Производство",
}

ROLE_PAGE = {
    "admin": "admin.html",
    "owner": "owner.html",
    "accountant": "accountant.html",
    "manager": "manager.html",
    "measurer": "measurer.html",
    "installer": "installer.html",
    "supply": "supply.html",
    "production": "production.html",
}

PRIVILEGED = frozenset({"admin", "owner"})

CSRF_SESSION_KEY = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"

# Login rate limit: 5 attempts / 15 minutes per client key (IP + email).
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW_SEC = 15 * 60

_login_lock = threading.Lock()
_login_attempts: dict[str, list[float]] = {}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except Exception:
        return False


def generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
    ]
    chars += [secrets.choice(alphabet) for _ in range(max(0, length - 3))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(expected: Optional[str], provided: Optional[str]) -> bool:
    if not expected or not provided:
        return False
    try:
        return secrets.compare_digest(expected, provided)
    except (TypeError, ValueError):
        return False


def login_rate_key(ip: str, email: str) -> str:
    return f"{(ip or 'unknown').strip()}|{(email or '').strip().lower()}"


def check_login_rate_limit(key: str) -> None:
    """Raise ValueError('rate_limited') if key already hit the cap this window.

    Call *before* processing login; on call we record the attempt.
    """
    now = time.time()
    with _login_lock:
        attempts = [t for t in _login_attempts.get(key, []) if now - t < LOGIN_RATE_WINDOW_SEC]
        if len(attempts) >= LOGIN_RATE_LIMIT:
            _login_attempts[key] = attempts
            raise ValueError("rate_limited")
        attempts.append(now)
        _login_attempts[key] = attempts


def clear_login_rate(key: str) -> None:
    with _login_lock:
        _login_attempts.pop(key, None)
