from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import db, validation
from ..security import hash_password
from .deps import SUPERADMIN, Principal, audit, conflict, not_found, require_superadmin

router = APIRouter(prefix="/admins", tags=["admins"])

SELECT_SQL = """
SELECT a.id, a.username, a.role, a.active, a.last_login, a.created_at, (a.totp_secret IS NOT NULL) AS is_2fa,
       COALESCE((SELECT GROUP_CONCAT(d.name ORDER BY d.name SEPARATOR ',')
                 FROM admin_domains x JOIN domains d ON d.id = x.domain_id WHERE x.admin_id = a.id), '') AS domains
FROM admins a
"""

Role = Literal["superadmin", "domainadmin"]


class AdminIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    role: Role = "superadmin"
    domains: list[str] = Field(default_factory=list, description="Domains a domainadmin manages")
    active: bool = True


class AdminPatch(BaseModel):
    password: str | None = Field(None, max_length=256)
    role: Role | None = None
    domains: list[str] | None = None
    active: bool | None = None
    disable_2fa: bool = False


def _out(row: dict) -> dict:
    row = db.clean(row)
    row["domains"] = row["domains"].split(",") if row["domains"] else []
    return row


def get_admin(cur, username: str) -> dict:
    cur.execute(SELECT_SQL + " WHERE a.username=%s", (username.lower(),))
    row = cur.fetchone()
    if not row:
        raise not_found("Admin")
    return _out(row)


def _set_domains(cur, admin_id: int, names: list[str]) -> None:
    wanted = sorted({validation.domain(n) for n in names})
    ids = []
    if wanted:
        cur.execute("SELECT id, name FROM domains WHERE name IN %s", (wanted,))
        found = {r["name"]: r["id"] for r in cur.fetchall()}
        missing = [n for n in wanted if n not in found]
        if missing:
            raise not_found("Domain " + ", ".join(missing))
        ids = list(found.values())
    cur.execute("DELETE FROM admin_domains WHERE admin_id=%s", (admin_id,))
    for domain_id in ids:
        cur.execute("INSERT INTO admin_domains (admin_id, domain_id) VALUES (%s, %s)", (admin_id, domain_id))


def _ensure_another_superadmin(cur, username: str) -> None:
    cur.execute(
        "SELECT COUNT(*) AS n FROM admins WHERE active=1 AND role=%s AND username<>%s",
        (SUPERADMIN, username),
    )
    if cur.fetchone()["n"] == 0:
        raise conflict("At least one active superadmin must remain")


@router.get("")
def list_admins(_: Principal = Depends(require_superadmin)):
    with db.transaction() as cur:
        cur.execute(SELECT_SQL + " ORDER BY a.username")
        return [_out(r) for r in cur.fetchall()]


@router.post("", status_code=201)
def create_admin(body: AdminIn, who: Principal = Depends(require_superadmin)):
    username = validation.admin_username(body.username)
    password = validation.password(body.password)
    with db.transaction() as cur:
        cur.execute("SELECT 1 FROM admins WHERE username=%s", (username,))
        if cur.fetchone():
            raise conflict(f"Admin {username} already exists")
        cur.execute(
            "INSERT INTO admins (username, password_hash, role, active) VALUES (%s, %s, %s, %s)",
            (username, hash_password(password), body.role, body.active),
        )
        if body.role != SUPERADMIN:
            _set_domains(cur, cur.lastrowid, body.domains)
        audit(cur, who, "admin.create", username, f"role={body.role} domains={','.join(body.domains)}")
        return get_admin(cur, username)


@router.patch("/{username}")
def update_admin(username: str, body: AdminPatch, who: Principal = Depends(require_superadmin)):
    with db.transaction() as cur:
        admin = get_admin(cur, username)
        notes = []
        demoting = body.role is not None and body.role != SUPERADMIN and admin["role"] == SUPERADMIN
        if (body.active is False or demoting) and admin["role"] == SUPERADMIN:
            _ensure_another_superadmin(cur, admin["username"])
        if body.password is not None:
            cur.execute(
                "UPDATE admins SET password_hash=%s WHERE id=%s",
                (hash_password(validation.password(body.password)), admin["id"]),
            )
            notes.append("password")
        if body.role is not None:
            cur.execute("UPDATE admins SET role=%s WHERE id=%s", (body.role, admin["id"]))
            notes.append(f"role={body.role}")
        role = body.role or admin["role"]
        if role == SUPERADMIN:
            cur.execute("DELETE FROM admin_domains WHERE admin_id=%s", (admin["id"],))
        elif body.domains is not None:
            _set_domains(cur, admin["id"], body.domains)
            notes.append("domains=" + ",".join(body.domains))
        if body.active is not None:
            cur.execute("UPDATE admins SET active=%s WHERE id=%s", (body.active, admin["id"]))
            notes.append(f"active={body.active}")
        if body.disable_2fa:
            cur.execute(
                "UPDATE admins SET totp_secret=NULL, totp_last_step=0, recovery_codes=NULL WHERE id=%s",
                (admin["id"],),
            )
            notes.append("2fa reset")
        audit(cur, who, "admin.update", admin["username"], ", ".join(notes))
        return get_admin(cur, admin["username"])


@router.delete("/{username}", status_code=204)
def delete_admin(username: str, who: Principal = Depends(require_superadmin)):
    with db.transaction() as cur:
        admin = get_admin(cur, username)
        if admin["username"] == who.username:
            raise conflict("You cannot delete your own account")
        if admin["role"] == SUPERADMIN:
            _ensure_another_superadmin(cur, admin["username"])
        cur.execute("DELETE FROM admins WHERE id=%s", (admin["id"],))
        audit(cur, who, "admin.delete", admin["username"])
