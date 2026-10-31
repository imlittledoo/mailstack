from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db, dkim, validation
from ..config import settings
from .deps import Principal, audit, ensure_scope, not_found, require_admin

router = APIRouter(prefix="/dkim", tags=["dkim"])


class DkimIn(BaseModel):
    selector: str | None = None
    bits: Literal[1024, 2048, 3072, 4096] | None = None


@router.get("")
def list_keys(who: Principal = Depends(require_admin)):
    """Every domain and alias domain you manage, with its key (or null)."""
    cond, args = who.scope("n.domain")
    rows = db.query(
        f"""SELECT n.domain, n.kind, k.selector, k.bits, k.public_key, k.created_at FROM (
             SELECT name AS domain, 'domain' AS kind FROM domains
             UNION SELECT alias_domain, 'alias' FROM alias_domains
           ) n LEFT JOIN dkim_keys k ON k.domain = n.domain WHERE {cond} ORDER BY n.domain""",
        args,
    )
    return [
        {"domain": r["domain"], "kind": r["kind"], "key": dkim.describe(r) if r["selector"] else None}
        for r in rows
    ]


@router.post("/{domain}")
def generate_key(domain: str, body: DkimIn, who: Principal = Depends(require_admin)):
    """Create or rotate the domain's key. Update DNS before mail is signed with the new key!"""
    domain = domain.lower()
    ensure_scope(who, domain)
    selector = validation.selector(body.selector or settings.dkim_selector)
    with db.transaction() as cur:
        cur.execute(
            "SELECT 1 FROM domains WHERE name=%s UNION SELECT 1 FROM alias_domains WHERE alias_domain=%s",
            (domain, domain),
        )
        if not cur.fetchone():
            raise not_found("Domain")
        dkim.create_key(cur, domain, selector, body.bits)
        audit(cur, who, "dkim.generate", domain, f"selector={selector} bits={body.bits or settings.dkim_key_bits}")
        cur.execute("SELECT * FROM dkim_keys WHERE domain=%s", (domain,))
        row = db.clean(cur.fetchone())
    dkim.sync()
    return dkim.describe(row)


@router.delete("/{domain}", status_code=204)
def delete_key(domain: str, who: Principal = Depends(require_admin)):
    ensure_scope(who, domain.lower())
    with db.transaction() as cur:
        cur.execute("DELETE FROM dkim_keys WHERE domain=%s", (domain.lower(),))
        if not cur.rowcount:
            raise not_found("DKIM key")
        audit(cur, who, "dkim.delete", domain.lower())
    dkim.sync()
