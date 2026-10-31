"""DKIM key management.

The database is the source of truth. `sync_files` mirrors keys into the shared
volume Rspamd reads: one `<domain>.<selector>.key` per domain plus
`selectors.map` (domain -> selector), which Rspamd reloads automatically.
"""

import base64
import logging
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from . import db
from .config import settings

log = logging.getLogger("mailstack.dkim")


def generate_keypair(bits: int) -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, base64.b64encode(public_der).decode()


def txt_value(public_key: str) -> str:
    return f"v=DKIM1; k=rsa; p={public_key}"


def describe(row: dict) -> dict:
    return {
        "domain": row["domain"],
        "selector": row["selector"],
        "bits": row["bits"],
        "created_at": row["created_at"],
        "dns_name": f"{row['selector']}._domainkey.{row['domain']}",
        "dns_value": txt_value(row["public_key"]),
    }


def create_key(cur, domain: str, selector: str | None = None, bits: int | None = None) -> None:
    selector = selector or settings.dkim_selector
    bits = bits or settings.dkim_key_bits
    private_pem, public_key = generate_keypair(bits)
    cur.execute(
        """INSERT INTO dkim_keys (domain, selector, bits, private_key, public_key)
           VALUES (%s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE selector=VALUES(selector), bits=VALUES(bits),
             private_key=VALUES(private_key), public_key=VALUES(public_key), created_at=NOW()""",
        (domain, selector, bits, private_pem, public_key),
    )


def _write_atomic(path: Path, content: str, mode: int) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="ascii") as fh:
        fh.write(content)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def sync_files(cur) -> None:
    directory = Path(settings.dkim_dir)
    if not directory.is_dir():
        log.warning("DKIM directory %s is missing, skipping sync", directory)
        return
    cur.execute("SELECT domain, selector, private_key FROM dkim_keys ORDER BY domain")
    rows = cur.fetchall()

    wanted = set()
    for row in rows:
        path = directory / f"{row['domain']}.{row['selector']}.key"
        wanted.add(path.name)
        if not path.exists() or path.read_text(encoding="ascii") != row["private_key"]:
            _write_atomic(path, row["private_key"], 0o640)

    selectors = "".join(f"{row['domain']} {row['selector']}\n" for row in rows)
    map_path = directory / "selectors.map"
    if not map_path.exists() or map_path.read_text(encoding="ascii") != selectors:
        _write_atomic(map_path, selectors, 0o640)

    for path in directory.glob("*.key"):
        if path.name not in wanted:
            path.unlink(missing_ok=True)
    log.info("DKIM keys synced (%d domains)", len(rows))


def sync() -> None:
    """Sync files from committed state. Call after the transaction that changed keys."""
    with db.transaction() as cur:
        sync_files(cur)
