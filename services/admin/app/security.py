import hashlib
import logging
import secrets
import threading
import time
from collections import defaultdict, deque

import bcrypt

from .config import settings

log = logging.getLogger("mailstack.security")

SCHEME = "{BLF-CRYPT}"


def hash_password(password: str) -> str:
    """bcrypt, stored with Dovecot's scheme prefix so Dovecot can verify it directly."""
    return SCHEME + bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, stored: str) -> bool:
    hashed = stored
    if hashed.startswith("{"):
        scheme, _, hashed = hashed[1:].partition("}")
        if scheme.upper() not in ("BLF-CRYPT", "BCRYPT"):
            return False
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def fingerprint(password_hash: str, totp_secret: str | None = None) -> str:
    """Bound to the session: changing the password or 2FA invalidates existing sessions."""
    material = f"{settings.session_secret}|{password_hash}|{totp_secret or ''}"
    return hashlib.sha256(material.encode()).hexdigest()[:24]


class LoginThrottle:
    """Per-IP sliding window of failed logins (single worker process)."""

    def __init__(self, limit: int = 10, window: int = 900):
        self.limit = limit
        self.window = window
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, ip: str) -> deque:
        q = self._failures[ip]
        cutoff = time.monotonic() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, ip: str) -> bool:
        with self._lock:
            return len(self._prune(ip)) >= self.limit

    def fail(self, ip: str) -> None:
        with self._lock:
            self._prune(ip).append(time.monotonic())

    def reset(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)


throttle = LoginThrottle()


def ensure_initial_admin(cur) -> None:
    cur.execute("SELECT COUNT(*) AS n FROM admins")
    if cur.fetchone()["n"]:
        return
    password = settings.admin_password
    if not password:
        password = secrets.token_urlsafe(15)
        log.warning("ADMIN_PASSWORD is empty - generated initial password for %r: %s", settings.admin_user, password)
    cur.execute(
        "INSERT INTO admins (username, password_hash, role) VALUES (%s, %s, 'superadmin')",
        (settings.admin_user.lower(), hash_password(password)),
    )
    log.info("created initial admin account %r", settings.admin_user)
