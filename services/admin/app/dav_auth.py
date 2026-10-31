"""Authentication backend for CalDAV/CardDAV.

nginx calls /internal/dav-auth (auth_request) for every /dav/ request and forwards the
verified user to Radicale. Accepts the mailbox password or any of its app passwords.
nginx refuses /internal/ from outside, so only the subrequest reaches this route.
"""

import base64
import binascii
import hashlib
import threading
import time

from fastapi import APIRouter, Request, Response

from . import db
from .api.deps import client_ip
from .api.me import app_password_hash
from .security import throttle, verify_password

router = APIRouter(include_in_schema=False)

CACHE_TTL = 120  # DAV clients send credentials with every request; bcrypt is deliberately slow.
_cache: dict[str, float] = {}
_lock = threading.Lock()

CHALLENGE = {"WWW-Authenticate": 'Basic realm="MailStack Calendar & Contacts", charset="UTF-8"'}


def _credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        user, sep, password = base64.b64decode(value).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return (user.strip().lower(), password) if sep and user else None


def _check(username: str, password: str) -> bool:
    row = db.query_one(
        """SELECT m.id, m.password_hash FROM mailboxes m JOIN domains d ON d.id = m.domain_id
           WHERE m.username=%s AND m.active=1 AND d.active=1""",
        (username,),
    )
    if row is None:
        return False
    app = db.query_one(
        "SELECT 1 AS ok FROM app_passwords WHERE mailbox_id=%s AND secret_sha256=%s",
        (row["id"], app_password_hash(password)),
    )
    return app is not None or verify_password(password, row["password_hash"])


@router.get("/internal/dav-auth")
def dav_auth(request: Request):
    creds = _credentials(request)
    if creds is None:
        return Response(status_code=401, headers=CHALLENGE)
    username, password = creds
    key = hashlib.sha256(f"{username}\0{password}".encode()).hexdigest()
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key, 0) > now
    if not cached:
        ip = client_ip(request)
        if throttle.blocked(ip):
            return Response(status_code=429)
        if not _check(username, password):
            throttle.fail(ip)
            return Response(status_code=401, headers=CHALLENGE)
        with _lock:
            for k in [k for k, exp in _cache.items() if exp <= now]:
                del _cache[k]
            _cache[key] = now + CACHE_TTL
    return Response(status_code=204, headers={"X-MailStack-User": username})
