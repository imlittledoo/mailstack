"""RFC 6238 TOTP (SHA1, 6 digits, 30 s) and single-use recovery codes."""

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote

import segno

STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return f"{value:0{DIGITS}d}"


def verify(secret: str, code: str, last_step: int = 0, window: int = 1) -> int | None:
    """Return the matched time step, or None. Steps <= last_step are rejected (no replay)."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    now = int(time.time()) // STEP
    for step in range(now - window, now + window + 1):
        if step > last_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def qr_data_uri(uri: str) -> str:
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace("-", "").encode()).hexdigest()


def new_recovery_codes(count: int = 10) -> tuple[list[str], str]:
    """Return (codes to show once, JSON of their hashes to store)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes, json.dumps([_hash_code(c) for c in codes])


def use_recovery_code(stored: str | None, code: str) -> str | None:
    """If code matches, return the updated JSON with it removed; otherwise None."""
    hashes = json.loads(stored or "[]")
    candidate = _hash_code(code)
    for h in hashes:
        if hmac.compare_digest(h, candidate):
            hashes.remove(h)
            return json.dumps(hashes)
    return None


def remaining_codes(stored: str | None) -> int:
    return len(json.loads(stored or "[]"))
