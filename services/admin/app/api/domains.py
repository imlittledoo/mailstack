from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import db, dkim, dnscheck, validation
from .deps import Principal, audit, conflict, ensure_scope, not_found, require_admin, require_superadmin
from .mailboxes import strip_goto_targets

router = APIRouter(prefix="/domains", tags=["domains"])

LIST_SQL = """
SELECT d.*,
  (SELECT COUNT(*) FROM mailboxes m WHERE m.domain_id = d.id) AS mailbox_count,
  (SELECT COALESCE(SUM(m.quota_mb), 0) FROM mailboxes m WHERE m.domain_id = d.id) AS quota_allocated_mb,
  (SELECT COUNT(*) FROM aliases a WHERE a.domain_id = d.id) AS alias_count,
  (SELECT COUNT(*) FROM alias_domains ad WHERE ad.target_domain_id = d.id) AS alias_domain_count,
  k.selector AS dkim_selector
FROM domains d LEFT JOIN dkim_keys k ON k.domain = d.name
"""


class DomainIn(BaseModel):
    name: str = Field(max_length=253)
    description: str = Field("", max_length=255)
    max_mailboxes: int = Field(0, ge=0)
    max_aliases: int = Field(0, ge=0)
    default_quota_mb: int = Field(1024, ge=0)
    max_quota_mb: int = Field(0, ge=0)
    total_quota_mb: int = Field(0, ge=0)
    active: bool = True
    generate_dkim: bool = True


class DomainPatch(BaseModel):
    description: str | None = Field(None, max_length=255)
    max_mailboxes: int | None = Field(None, ge=0)
    max_aliases: int | None = Field(None, ge=0)
    default_quota_mb: int | None = Field(None, ge=0)
    max_quota_mb: int | None = Field(None, ge=0)
    total_quota_mb: int | None = Field(None, ge=0)
    active: bool | None = None


def _check_limits(d: dict) -> None:
    if d["max_quota_mb"] and d["default_quota_mb"] > d["max_quota_mb"]:
        raise validation.invalid("Default mailbox quota cannot exceed the per-mailbox maximum")
    if d["total_quota_mb"] and d["max_quota_mb"] > d["total_quota_mb"]:
        raise validation.invalid("Per-mailbox maximum cannot exceed the domain total")
    if d["max_quota_mb"] and d["default_quota_mb"] == 0:
        raise validation.invalid("Default quota cannot be unlimited when a per-mailbox maximum is set")


def get_domain(cur, name: str) -> dict:
    cur.execute(LIST_SQL + " WHERE d.name = %s", (name,))
    row = cur.fetchone()
    if not row:
        raise not_found("Domain")
    return db.clean(row)


def name_in_use(cur, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM domains WHERE name=%s UNION SELECT 1 FROM alias_domains WHERE alias_domain=%s",
        (name, name),
    )
    return cur.fetchone() is not None


@router.get("")
def list_domains(who: Principal = Depends(require_admin)):
    cond, args = who.scope("d.name")
    return db.query(LIST_SQL + f" WHERE {cond} ORDER BY d.name", args)


@router.post("", status_code=201)
def create_domain(body: DomainIn, who: Principal = Depends(require_superadmin)):
    name = validation.domain(body.name)
    data = body.model_dump(exclude={"generate_dkim"}) | {"name": name}
    _check_limits(data)
    with db.transaction() as cur:
        if name_in_use(cur, name):
            raise conflict(f"{name} already exists (as a domain or alias domain)")
        cur.execute(
            """INSERT INTO domains (name, description, max_mailboxes, max_aliases,
                 default_quota_mb, max_quota_mb, total_quota_mb, active)
               VALUES (%(name)s, %(description)s, %(max_mailboxes)s, %(max_aliases)s,
                 %(default_quota_mb)s, %(max_quota_mb)s, %(total_quota_mb)s, %(active)s)""",
            data,
        )
        if body.generate_dkim:
            dkim.create_key(cur, name)
        audit(cur, who, "domain.create", name)
    dkim.sync()
    with db.transaction() as cur:
        return get_domain(cur, name)


@router.get("/{name}")
def read_domain(name: str, who: Principal = Depends(require_admin)):
    ensure_scope(who, name.lower())
    with db.transaction() as cur:
        return get_domain(cur, name.lower())


@router.patch("/{name}")
def update_domain(name: str, body: DomainPatch, who: Principal = Depends(require_superadmin)):
    changes = body.model_dump(exclude_none=True)
    with db.transaction() as cur:
        current = get_domain(cur, name.lower())
        merged = current | changes
        _check_limits(merged)
        if changes:
            assignments = ", ".join(f"{k}=%({k})s" for k in changes)
            cur.execute(f"UPDATE domains SET {assignments} WHERE id=%(id)s", changes | {"id": current["id"]})
            audit(cur, who, "domain.update", current["name"], ", ".join(f"{k}={v}" for k, v in changes.items()))
        return get_domain(cur, current["name"])


@router.delete("/{name}", status_code=204)
def delete_domain(name: str, who: Principal = Depends(require_superadmin)):
    with db.transaction() as cur:
        domain = get_domain(cur, name.lower())
        cur.execute("SELECT username FROM mailboxes WHERE domain_id=%s", (domain["id"],))
        usernames = [r["username"] for r in cur.fetchall()]
        cur.execute("SELECT alias_domain FROM alias_domains WHERE target_domain_id=%s", (domain["id"],))
        alias_domains = [r["alias_domain"] for r in cur.fetchall()]
        # Mailboxes, aliases and alias domains go with the domain (ON DELETE CASCADE).
        cur.execute("DELETE FROM domains WHERE id=%s", (domain["id"],))
        cur.execute("DELETE FROM dkim_keys WHERE domain IN %s", ([domain["name"], *alias_domains],))
        strip_goto_targets(cur, usernames)
        audit(cur, who, "domain.delete", domain["name"], f"{len(usernames)} mailboxes")
    dkim.sync()


@router.get("/{name}/dns")
def domain_dns(name: str, who: Principal = Depends(require_admin)):
    name = name.lower()
    ensure_scope(who, name)
    with db.transaction() as cur:
        if not name_in_use(cur, name):
            raise not_found("Domain")
        cur.execute("SELECT * FROM dkim_keys WHERE domain=%s", (name,))
        dkim_row = cur.fetchone()
    return {"domain": name, "records": dnscheck.records(name, dkim_row)}
