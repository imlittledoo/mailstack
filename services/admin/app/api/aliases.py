from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import db, validation
from .deps import Principal, audit, conflict, ensure_scope, not_found, require_admin

router = APIRouter(prefix="/aliases", tags=["aliases"])

SELECT_SQL = """
SELECT a.id, a.address, d.name AS domain, a.goto, a.description, a.active, a.created_at, a.updated_at,
       (a.address = CONCAT('@', d.name)) AS is_catch_all
FROM aliases a JOIN domains d ON d.id = a.domain_id
"""


class AliasIn(BaseModel):
    address: str = Field(max_length=320, description="user@domain, or @domain for a catch-all")
    goto: list[str] = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=255)
    active: bool = True


class AliasPatch(BaseModel):
    goto: list[str] | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=255)
    active: bool | None = None


def _out(row: dict) -> dict:
    row = db.clean(row)
    row["goto"] = row["goto"].split(",")
    return row


def _parse_goto(values: list[str]) -> str:
    targets = []
    for v in values:
        for part in v.replace(";", ",").replace(" ", ",").split(","):
            if part.strip():
                addr = validation.email(part, external=True)
                if addr not in targets:
                    targets.append(addr)
    if not targets:
        raise validation.invalid("At least one destination address is required")
    return ",".join(targets)


def get_alias(cur, alias_id: int) -> dict:
    cur.execute(SELECT_SQL + " WHERE a.id = %s", (alias_id,))
    row = cur.fetchone()
    if not row:
        raise not_found("Alias")
    return _out(row)


@router.get("")
def list_aliases(domain: str | None = None, who: Principal = Depends(require_admin)):
    cond, args = who.scope("d.name")
    sql = SELECT_SQL + f" WHERE {cond}"
    if domain:
        sql += " AND d.name = %s"
        args.append(domain.lower())
    with db.transaction() as cur:
        cur.execute(sql + " ORDER BY d.name, a.address", args)
        return [_out(r) for r in cur.fetchall()]


@router.post("", status_code=201)
def create_alias(body: AliasIn, who: Principal = Depends(require_admin)):
    raw = body.address.strip().lower()
    if raw.startswith("@"):
        domain_name = validation.domain(raw[1:])
        address = "@" + domain_name
    else:
        address = validation.email(raw)
        domain_name = address.split("@", 1)[1]
    goto = _parse_goto(body.goto)
    if address in goto.split(","):
        raise validation.invalid("An alias cannot deliver to itself")

    ensure_scope(who, domain_name)
    with db.transaction() as cur:
        cur.execute("SELECT * FROM domains WHERE name=%s FOR UPDATE", (domain_name,))
        domain = cur.fetchone()
        if not domain:
            raise not_found(f"Domain {domain_name}")
        if domain["max_aliases"]:
            cur.execute("SELECT COUNT(*) AS n FROM aliases WHERE domain_id=%s", (domain["id"],))
            if cur.fetchone()["n"] >= domain["max_aliases"]:
                raise conflict(f"Domain alias limit reached ({domain['max_aliases']})")
        cur.execute(
            "SELECT 1 FROM aliases WHERE address=%s UNION SELECT 1 FROM mailboxes WHERE username=%s",
            (address, address),
        )
        if cur.fetchone():
            raise conflict(f"{address} already exists as an alias or mailbox")
        cur.execute(
            "INSERT INTO aliases (domain_id, address, goto, description, active) VALUES (%s, %s, %s, %s, %s)",
            (domain["id"], address, goto, body.description.strip(), body.active),
        )
        alias_id = cur.lastrowid
        audit(cur, who, "alias.create", address, f"-> {goto}")
        return get_alias(cur, alias_id)


@router.patch("/{alias_id}")
def update_alias(alias_id: int, body: AliasPatch, who: Principal = Depends(require_admin)):
    with db.transaction() as cur:
        alias = get_alias(cur, alias_id)
        ensure_scope(who, alias["domain"])
        sets, args = [], []
        if body.goto is not None:
            goto = _parse_goto(body.goto)
            if alias["address"] in goto.split(","):
                raise validation.invalid("An alias cannot deliver to itself")
            sets.append("goto=%s"); args.append(goto)
        if body.description is not None:
            sets.append("description=%s"); args.append(body.description.strip())
        if body.active is not None:
            sets.append("active=%s"); args.append(body.active)
        if sets:
            cur.execute(f"UPDATE aliases SET {', '.join(sets)} WHERE id=%s", (*args, alias_id))
            audit(cur, who, "alias.update", alias["address"])
        return get_alias(cur, alias_id)


@router.delete("/{alias_id}", status_code=204)
def delete_alias(alias_id: int, who: Principal = Depends(require_admin)):
    with db.transaction() as cur:
        alias = get_alias(cur, alias_id)
        ensure_scope(who, alias["domain"])
        cur.execute("DELETE FROM aliases WHERE id=%s", (alias_id,))
        audit(cur, who, "alias.delete", alias["address"])
