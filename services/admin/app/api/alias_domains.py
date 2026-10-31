from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import db, dkim, validation
from .deps import Principal, audit, conflict, not_found, require_admin, require_superadmin
from .domains import name_in_use

router = APIRouter(prefix="/alias-domains", tags=["alias domains"])

SELECT_SQL = """
SELECT ad.id, ad.alias_domain, d.name AS target_domain, ad.active, ad.created_at, k.selector AS dkim_selector
FROM alias_domains ad
JOIN domains d ON d.id = ad.target_domain_id
LEFT JOIN dkim_keys k ON k.domain = ad.alias_domain
"""


class AliasDomainIn(BaseModel):
    alias_domain: str = Field(max_length=253)
    target_domain: str = Field(max_length=253)
    active: bool = True
    generate_dkim: bool = True


class AliasDomainPatch(BaseModel):
    active: bool


def get_alias_domain(cur, name: str) -> dict:
    cur.execute(SELECT_SQL + " WHERE ad.alias_domain = %s", (name.lower(),))
    row = cur.fetchone()
    if not row:
        raise not_found("Alias domain")
    return db.clean(row)


@router.get("")
def list_alias_domains(who: Principal = Depends(require_admin)):
    cond, args = who.scope("d.name")
    return db.query(SELECT_SQL + f" WHERE {cond} ORDER BY ad.alias_domain", args)


@router.post("", status_code=201)
def create_alias_domain(body: AliasDomainIn, who: Principal = Depends(require_superadmin)):
    alias = validation.domain(body.alias_domain)
    target = validation.domain(body.target_domain)
    with db.transaction() as cur:
        cur.execute("SELECT id FROM domains WHERE name=%s", (target,))
        target_row = cur.fetchone()
        if not target_row:
            raise not_found(f"Target domain {target}")
        if name_in_use(cur, alias):
            raise conflict(f"{alias} already exists (as a domain or alias domain)")
        cur.execute(
            "INSERT INTO alias_domains (alias_domain, target_domain_id, active) VALUES (%s, %s, %s)",
            (alias, target_row["id"], body.active),
        )
        if body.generate_dkim:
            dkim.create_key(cur, alias)
        audit(cur, who, "alias_domain.create", alias, f"-> {target}")
    dkim.sync()
    with db.transaction() as cur:
        return get_alias_domain(cur, alias)


@router.patch("/{name}")
def update_alias_domain(name: str, body: AliasDomainPatch, who: Principal = Depends(require_superadmin)):
    with db.transaction() as cur:
        row = get_alias_domain(cur, name)
        cur.execute("UPDATE alias_domains SET active=%s WHERE id=%s", (body.active, row["id"]))
        audit(cur, who, "alias_domain.update", row["alias_domain"], f"active={body.active}")
        return get_alias_domain(cur, row["alias_domain"])


@router.delete("/{name}", status_code=204)
def delete_alias_domain(name: str, who: Principal = Depends(require_superadmin)):
    with db.transaction() as cur:
        row = get_alias_domain(cur, name)
        cur.execute("DELETE FROM alias_domains WHERE id=%s", (row["id"],))
        cur.execute("DELETE FROM dkim_keys WHERE domain=%s", (row["alias_domain"],))
        audit(cur, who, "alias_domain.delete", row["alias_domain"])
    dkim.sync()
