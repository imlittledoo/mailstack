import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db, totp
from ..config import settings
from ..security import fingerprint, throttle, verify_password
from .deps import Principal, account_row, audit, client_ip, get_principal, principal_for

router = APIRouter(prefix="/auth", tags=["auth"])

PENDING_TTL = 300


class LoginIn(BaseModel):
    username: str = Field(max_length=320)
    password: str = Field(max_length=256)


class CodeIn(BaseModel):
    code: str = Field(max_length=32, description="6-digit authenticator code or a recovery code")


def me_payload(p: Principal) -> dict:
    return {
        "role": p.role,
        "username": p.username,
        "superadmin": p.superadmin,
        "domains": sorted(p.domains) if p.domains is not None else None,
        "mail_hostname": settings.mail_hostname,
    }


def _table(role: str) -> str:
    return "admins" if role == "admin" else "mailboxes"


def _fail(ip: str, username: str, detail: str, action: str = "login.failed") -> HTTPException:
    throttle.fail(ip)
    with db.transaction() as cur:
        audit(cur, Principal("-", username, ip), action, username)
    return HTTPException(status_code=401, detail=detail)


def _finish(request: Request, role: str, row: dict, ip: str, note: str = "") -> dict:
    principal = principal_for(role, row, ip)
    with db.transaction() as cur:
        cur.execute(f"UPDATE {_table(role)} SET last_login=NOW() WHERE id=%s", (row["id"],))
        audit(cur, principal, "login", principal.username, note)
    throttle.reset(ip)
    request.session.clear()
    request.session["auth"] = {
        "role": role,
        "username": principal.username,
        "fp": fingerprint(row["password_hash"], row["totp_secret"]),
    }
    return me_payload(principal)


@router.post("/login")
def login(body: LoginIn, request: Request):
    """Admins sign in with their username, mailbox users with their email address.

    If two-factor auth is enabled the response is {"totp_required": true}; finish with /auth/login/totp.
    """
    ip = client_ip(request)
    if throttle.blocked(ip):
        raise HTTPException(status_code=429, detail="Too many failed logins. Try again in 15 minutes.")

    username = body.username.strip().lower()
    role = "user" if "@" in username else "admin"
    row = account_row(role, username)
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise _fail(ip, username, "Invalid username or password")

    if row["totp_secret"]:
        request.session.clear()
        request.session["pending"] = {"role": role, "username": row["username"], "ts": time.time()}
        return {"totp_required": True}
    return _finish(request, role, row, ip)


@router.post("/login/totp")
def login_totp(body: CodeIn, request: Request):
    ip = client_ip(request)
    if throttle.blocked(ip):
        raise HTTPException(status_code=429, detail="Too many failed logins. Try again in 15 minutes.")
    pending = request.session.get("pending")
    if not isinstance(pending, dict) or time.time() - pending.get("ts", 0) > PENDING_TTL:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Sign-in expired, please start again")

    role, username = pending["role"], pending["username"]
    row = account_row(role, username)
    if row is None or not row["totp_secret"]:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Sign-in expired, please start again")

    table = _table(role)
    step = totp.verify(row["totp_secret"], body.code, row["totp_last_step"])
    if step is not None:
        with db.transaction() as cur:
            cur.execute(f"UPDATE {table} SET totp_last_step=%s WHERE id=%s", (step, row["id"]))
        return _finish(request, role, row, ip)

    remaining = totp.use_recovery_code(row["recovery_codes"], body.code)
    if remaining is not None:
        with db.transaction() as cur:
            cur.execute(f"UPDATE {table} SET recovery_codes=%s WHERE id=%s", (remaining, row["id"]))
        return _finish(request, role, row, ip, f"recovery code used, {totp.remaining_codes(remaining)} left")

    raise _fail(ip, username, "Invalid authentication code", "login.failed_2fa")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    principal = get_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return me_payload(principal)
