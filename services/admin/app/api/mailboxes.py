from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import db, services, validation
from ..security import hash_password
from .deps import Principal, audit, conflict, ensure_scope, not_found, require_admin

router = APIRouter(prefix="/mailboxes", tags=["mailboxes"])

SELECT_SQL = """
SELECT m.id, m.username, m.local_part, d.name AS domain, m.name, m.quota_mb, m.active,
       m.last_login, m.created_at, m.updated_at, (m.totp_secret IS NOT NULL) AS is_2fa,
       (SELECT COUNT(*) FROM app_passwords ap WHERE ap.mailbox_id = m.id) AS app_passwords
FROM mailboxes m JOIN domains d ON d.id = m.domain_id
"""


class MailboxIn(BaseModel):
    local_part: str = Field(max_length=64)
    domain: str = Field(max_length=253)
    name: str = Field("", max_length=255)
    password: str = Field(max_length=256)
    quota_mb: int | None = Field(None, ge=0, description="Defaults to the domain's default quota")
    active: bool = True


class MailboxPatch(BaseModel):
    name: str | None = Field(None, max_length=255)
    password: str | None = Field(None, max_length=256)
    quota_mb: int | None = Field(None, ge=0)
    active: bool | None = None
    disable_2fa: bool = False
    revoke_app_passwords: bool = False


def strip_goto_targets(cur, usernames: list[str]) -> None:
    """Remove deleted mailboxes from alias destinations; drop aliases left with none."""
    if not usernames:
        return
    gone = set(usernames)
    cur.execute("SELECT id, goto FROM aliases")
    for row in cur.fetchall():
        targets = row["goto"].split(",")
        kept = [t for t in targets if t not in gone]
        if len(kept) == len(targets):
            continue
        if kept:
            cur.execute("UPDATE aliases SET goto=%s WHERE id=%s", (",".join(kept), row["id"]))
        else:
            cur.execute("DELETE FROM aliases WHERE id=%s", (row["id"],))


def check_quota(cur, domain: dict, quota_mb: int, exclude_id: int | None = None) -> None:
    if quota_mb == 0 and (domain["max_quota_mb"] or domain["total_quota_mb"]):
        raise validation.invalid("Unlimited quota is not allowed for this domain")
    if domain["max_quota_mb"] and quota_mb > domain["max_quota_mb"]:
        raise validation.invalid(f"Quota exceeds the domain's per-mailbox maximum of {domain['max_quota_mb']} MB")
    if domain["total_quota_mb"]:
        cur.execute(
            "SELECT COALESCE(SUM(quota_mb), 0) AS used FROM mailboxes WHERE domain_id=%s AND id <> %s",
            (domain["id"], exclude_id or 0),
        )
        used = int(cur.fetchone()["used"])
        if used + quota_mb > domain["total_quota_mb"]:
            free = domain["total_quota_mb"] - used
            raise validation.invalid(f"Domain quota exhausted: {free} MB of {domain['total_quota_mb']} MB left")


def get_mailbox(cur, username: str) -> dict:
    cur.execute(SELECT_SQL + " WHERE m.username = %s", (username.lower(),))
    row = cur.fetchone()
    if not row:
        raise not_found("Mailbox")
    return db.clean(row)


def with_usage(rows: list[dict]) -> list[dict]:
    usage = services.quota_usage([r["username"] for r in rows])
    for r in rows:
        u = usage.get(r["username"])
        r["used_bytes"] = u["used_bytes"] if u else None
        r["messages"] = u["messages"] if u else None
    return rows


@router.get("")
def list_mailboxes(
    domain: str | None = None,
    q: str | None = Query(None, max_length=320),
    usage: bool = True,
    who: Principal = Depends(require_admin),
):
    cond, args = who.scope("d.name")
    sql = SELECT_SQL + f" WHERE {cond}"
    if domain:
        sql += " AND d.name = %s"
        args.append(domain.lower())
    if q:
        sql += " AND (m.username LIKE %s OR m.name LIKE %s)"
        args += [f"%{q}%", f"%{q}%"]
    rows = db.query(sql + " ORDER BY d.name, m.local_part", args)
    return with_usage(rows) if usage else rows


@router.post("", status_code=201)
def create_mailbox(body: MailboxIn, who: Principal = Depends(require_admin)):
    local = validation.local_part(body.local_part)
    domain_name = validation.domain(body.domain)
    password = validation.password(body.password)
    username = f"{local}@{domain_name}"
    ensure_scope(who, domain_name)
    with db.transaction() as cur:
        cur.execute("SELECT * FROM domains WHERE name=%s FOR UPDATE", (domain_name,))
        domain = cur.fetchone()
        if not domain:
            raise not_found("Domain")
        if domain["max_mailboxes"]:
            cur.execute("SELECT COUNT(*) AS n FROM mailboxes WHERE domain_id=%s", (domain["id"],))
            if cur.fetchone()["n"] >= domain["max_mailboxes"]:
                raise conflict(f"Domain mailbox limit reached ({domain['max_mailboxes']})")
        cur.execute(
            "SELECT 1 FROM mailboxes WHERE username=%s UNION SELECT 1 FROM aliases WHERE address=%s",
            (username, username),
        )
        if cur.fetchone():
            raise conflict(f"{username} already exists as a mailbox or alias")
        quota = domain["default_quota_mb"] if body.quota_mb is None else body.quota_mb
        check_quota(cur, domain, quota)
        cur.execute(
            """INSERT INTO mailboxes (domain_id, local_part, username, password_hash, name, quota_mb, active)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (domain["id"], local, username, hash_password(password), body.name.strip(), quota, body.active),
        )
        audit(cur, who, "mailbox.create", username, f"quota={quota}MB")
        return get_mailbox(cur, username)


@router.get("/{username}")
def read_mailbox(username: str, who: Principal = Depends(require_admin)):
    with db.transaction() as cur:
        row = get_mailbox(cur, username)
    ensure_scope(who, row["domain"])
    return with_usage([row])[0]


@router.patch("/{username}")
def update_mailbox(username: str, body: MailboxPatch, who: Principal = Depends(require_admin)):
    with db.transaction() as cur:
        mbox = get_mailbox(cur, username)
        ensure_scope(who, mbox["domain"])
        sets, args, notes = [], [], []
        if body.name is not None:
            sets.append("name=%s"); args.append(body.name.strip()); notes.append("name")
        if body.password is not None:
            sets.append("password_hash=%s"); args.append(hash_password(validation.password(body.password))); notes.append("password")
        if body.quota_mb is not None:
            cur.execute("SELECT * FROM domains WHERE name=%s", (mbox["domain"],))
            check_quota(cur, cur.fetchone(), body.quota_mb, exclude_id=mbox["id"])
            sets.append("quota_mb=%s"); args.append(body.quota_mb); notes.append(f"quota={body.quota_mb}MB")
        if body.active is not None:
            sets.append("active=%s"); args.append(body.active); notes.append(f"active={body.active}")
        if body.disable_2fa:
            sets.append("totp_secret=NULL, totp_last_step=0, recovery_codes=NULL"); notes.append("2fa reset")
        if body.revoke_app_passwords:
            cur.execute("DELETE FROM app_passwords WHERE mailbox_id=%s", (mbox["id"],)); notes.append("app passwords revoked")
        if sets:
            cur.execute(f"UPDATE mailboxes SET {', '.join(sets)} WHERE id=%s", (*args, mbox["id"]))
        if notes:
            audit(cur, who, "mailbox.update", mbox["username"], ", ".join(notes))
        return get_mailbox(cur, mbox["username"])


@router.delete("/{username}", status_code=204)
def delete_mailbox(username: str, who: Principal = Depends(require_admin)):
    with db.transaction() as cur:
        mbox = get_mailbox(cur, username)
        ensure_scope(who, mbox["domain"])
        cur.execute("DELETE FROM mailboxes WHERE id=%s", (mbox["id"],))
        strip_goto_targets(cur, [mbox["username"]])
        audit(cur, who, "mailbox.delete", mbox["username"])
