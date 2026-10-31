"""Security settings of the signed-in account (admin or mailbox): password and two-factor auth."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db, totp, validation
from ..config import settings
from ..security import fingerprint, hash_password, verify_password
from .deps import Principal, account_row, audit, require_session

router = APIRouter(prefix="/account", tags=["account"])


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class EnableIn(BaseModel):
    code: str = Field(max_length=16)
    password: str = Field(max_length=256)


class ConfirmIn(BaseModel):
    password: str = Field(max_length=256)


def _table(who: Principal) -> str:
    return "admins" if who.role == "admin" else "mailboxes"


def _verified_row(who: Principal, password: str) -> dict:
    row = account_row(who.role, who.username)
    if row is None or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=403, detail="Password is incorrect")
    return row


def _refresh_session(request: Request, who: Principal) -> None:
    """Keep this session valid after a change that invalidates all others."""
    row = account_row(who.role, who.username)
    request.session["auth"] = {
        "role": who.role,
        "username": who.username,
        "fp": fingerprint(row["password_hash"], row["totp_secret"]),
    }


@router.get("")
def account(who: Principal = Depends(require_session)):
    row = account_row(who.role, who.username)
    return {
        "username": who.username,
        "role": who.role,
        "totp_enabled": bool(row["totp_secret"]),
        "recovery_codes_left": totp.remaining_codes(row["recovery_codes"]) if row["totp_secret"] else 0,
    }


@router.post("/password")
def change_password(body: PasswordIn, request: Request, who: Principal = Depends(require_session)):
    """Change your password. Every other session of this account is signed out."""
    new = validation.password(body.new_password)
    row = _verified_row(who, body.current_password)
    with db.transaction() as cur:
        cur.execute(f"UPDATE {_table(who)} SET password_hash=%s WHERE id=%s", (hash_password(new), row["id"]))
        audit(cur, who, "account.password", who.username)
    _refresh_session(request, who)
    return {"ok": True}


@router.post("/2fa/setup")
def setup_2fa(request: Request, who: Principal = Depends(require_session)):
    """Start enrolment: returns a fresh secret and QR code. Nothing changes until /2fa/enable."""
    secret = totp.new_secret()
    request.session["totp_setup"] = secret
    uri = totp.provisioning_uri(secret, who.username, settings.mail_hostname)
    return {"secret": secret, "uri": uri, "qr": totp.qr_data_uri(uri)}


@router.post("/2fa/enable")
def enable_2fa(body: EnableIn, request: Request, who: Principal = Depends(require_session)):
    secret = request.session.get("totp_setup")
    if not secret:
        raise HTTPException(status_code=400, detail="Start the setup again")
    row = _verified_row(who, body.password)
    step = totp.verify(secret, body.code)
    if step is None:
        raise HTTPException(status_code=422, detail="The code does not match - check your device's clock and try again")
    codes, hashes = totp.new_recovery_codes()
    with db.transaction() as cur:
        cur.execute(
            f"UPDATE {_table(who)} SET totp_secret=%s, totp_last_step=%s, recovery_codes=%s WHERE id=%s",
            (secret, step, hashes, row["id"]),
        )
        audit(cur, who, "2fa.enable", who.username)
    request.session.pop("totp_setup", None)
    _refresh_session(request, who)
    return {"recovery_codes": codes}


@router.post("/2fa/recovery-codes")
def regenerate_codes(body: ConfirmIn, who: Principal = Depends(require_session)):
    row = _verified_row(who, body.password)
    if not row["totp_secret"]:
        raise HTTPException(status_code=400, detail="Two-factor authentication is not enabled")
    codes, hashes = totp.new_recovery_codes()
    with db.transaction() as cur:
        cur.execute(f"UPDATE {_table(who)} SET recovery_codes=%s WHERE id=%s", (hashes, row["id"]))
        audit(cur, who, "2fa.recovery_codes", who.username)
    return {"recovery_codes": codes}


@router.post("/2fa/disable")
def disable_2fa(body: ConfirmIn, request: Request, who: Principal = Depends(require_session)):
    row = _verified_row(who, body.password)
    with db.transaction() as cur:
        cur.execute(
            f"UPDATE {_table(who)} SET totp_secret=NULL, totp_last_step=0, recovery_codes=NULL WHERE id=%s",
            (row["id"],),
        )
        audit(cur, who, "2fa.disable", who.username)
    _refresh_session(request, who)
    return {"ok": True}
