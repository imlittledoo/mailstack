"""Self-service for mailbox users."""

import hashlib
import plistlib
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import db
from ..config import settings
from .deps import Principal, audit, not_found, require_user
from .mailboxes import SELECT_SQL, with_usage

router = APIRouter(prefix="/me", tags=["self-service"])


class AppPasswordIn(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="e.g. 'Phone' or 'Thunderbird laptop'")


def app_password_hash(secret: str) -> str:
    """App passwords are random, so an unsalted SHA-256 is safe - and Dovecot can match it in SQL."""
    return hashlib.sha256(secret.encode()).hexdigest()


def client_settings() -> dict:
    h = settings.mail_hostname
    return {
        "imap": {"host": h, "port": 993, "security": "SSL/TLS"},
        "pop3": {"host": h, "port": 995, "security": "SSL/TLS"},
        "smtp": {"host": h, "port": 465, "security": "SSL/TLS", "alt_port": 587, "alt_security": "STARTTLS"},
        "sieve": {"host": h, "port": 4190, "security": "STARTTLS"},
        "webmail": f"https://{h}/webmail/",
        "dav": f"https://{h}/dav/",
    }


@router.get("")
def my_mailbox(who: Principal = Depends(require_user)):
    with db.transaction() as cur:
        cur.execute(SELECT_SQL + " WHERE m.username=%s", (who.username,))
        mbox = db.clean(cur.fetchone())
        cur.execute(
            "SELECT address FROM aliases WHERE active=1 AND FIND_IN_SET(%s, goto) > 0 ORDER BY address",
            (who.username,),
        )
        aliases = [r["address"] for r in cur.fetchall()]
        cur.execute(
            """SELECT ad.alias_domain FROM alias_domains ad JOIN domains d ON d.id = ad.target_domain_id
               WHERE d.name=%s AND ad.active=1""",
            (mbox["domain"],),
        )
        alias_addresses = [f"{mbox['local_part']}@{r['alias_domain']}" for r in cur.fetchall()]
    mbox = with_usage([mbox])[0]
    mbox["aliases"] = aliases + alias_addresses
    mbox["client"] = client_settings()
    return mbox


@router.get("/app-passwords")
def list_app_passwords(who: Principal = Depends(require_user)):
    return db.query(
        """SELECT ap.id, ap.name, ap.created_at FROM app_passwords ap
           JOIN mailboxes m ON m.id = ap.mailbox_id WHERE m.username=%s ORDER BY ap.created_at""",
        (who.username,),
    )


@router.post("/app-passwords", status_code=201)
def create_app_password(body: AppPasswordIn, who: Principal = Depends(require_user)):
    """Create a password for one mail/calendar app. It is shown only once."""
    alphabet = "abcdefghijkmnopqrstuvwxyz23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(20))
    secret = "-".join(raw[i:i + 5] for i in range(0, 20, 5))
    with db.transaction() as cur:
        cur.execute("SELECT id FROM mailboxes WHERE username=%s", (who.username,))
        mailbox_id = cur.fetchone()["id"]
        cur.execute("SELECT COUNT(*) AS n FROM app_passwords WHERE mailbox_id=%s", (mailbox_id,))
        if cur.fetchone()["n"] >= 50:
            raise HTTPException(status_code=409, detail="Too many app passwords - revoke unused ones first")
        cur.execute(
            "INSERT INTO app_passwords (mailbox_id, name, secret_sha256) VALUES (%s, %s, %s)",
            (mailbox_id, body.name.strip(), app_password_hash(secret)),
        )
        password_id = cur.lastrowid
        audit(cur, who, "app_password.create", who.username, body.name.strip())
    return {"id": password_id, "name": body.name.strip(), "password": secret}


@router.delete("/app-passwords/{password_id}", status_code=204)
def delete_app_password(password_id: int, who: Principal = Depends(require_user)):
    with db.transaction() as cur:
        cur.execute(
            """DELETE ap FROM app_passwords ap JOIN mailboxes m ON m.id = ap.mailbox_id
               WHERE ap.id=%s AND m.username=%s""",
            (password_id, who.username),
        )
        if not cur.rowcount:
            raise not_found("App password")
        audit(cur, who, "app_password.delete", who.username, str(password_id))


@router.get("/mobileconfig")
def apple_profile(who: Principal = Depends(require_user)):
    """Configuration profile for iOS / macOS Mail (password is asked on install)."""
    row = db.query_one("SELECT name FROM mailboxes WHERE username=%s", (who.username,))
    host = settings.mail_hostname
    ident = ".".join(reversed(host.split(".")))
    account = {
        "EmailAccountDescription": who.username,
        "EmailAccountName": (row or {}).get("name") or who.username,
        "EmailAccountType": "EmailTypeIMAP",
        "EmailAddress": who.username,
        "IncomingMailServerAuthentication": "EmailAuthPassword",
        "IncomingMailServerHostName": host,
        "IncomingMailServerPortNumber": 993,
        "IncomingMailServerUseSSL": True,
        "IncomingMailServerUsername": who.username,
        "OutgoingMailServerAuthentication": "EmailAuthPassword",
        "OutgoingMailServerHostName": host,
        "OutgoingMailServerPortNumber": 465,
        "OutgoingMailServerUseSSL": True,
        "OutgoingMailServerUsername": who.username,
        "OutgoingPasswordSameAsIncomingPassword": True,
        "PayloadDescription": f"Email account {who.username}",
        "PayloadDisplayName": who.username,
        "PayloadIdentifier": f"{ident}.mailstack.account.{uuid.uuid5(uuid.NAMESPACE_DNS, who.username)}",
        "PayloadType": "com.apple.mail.managed",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadVersion": 1,
    }
    dav_url = f"https://{host}/dav/{who.username}/"
    caldav = {
        "CalDAVAccountDescription": f"Calendar {who.username}",
        "CalDAVHostName": host,
        "CalDAVPort": 443,
        "CalDAVUseSSL": True,
        "CalDAVUsername": who.username,
        "CalDAVPrincipalURL": dav_url,
        "PayloadDisplayName": "Calendar",
        "PayloadIdentifier": f"{ident}.mailstack.caldav.{uuid.uuid5(uuid.NAMESPACE_DNS, who.username)}",
        "PayloadType": "com.apple.caldav.account",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadVersion": 1,
    }
    carddav = {
        "CardDAVAccountDescription": f"Contacts {who.username}",
        "CardDAVHostName": host,
        "CardDAVPort": 443,
        "CardDAVUseSSL": True,
        "CardDAVUsername": who.username,
        "CardDAVPrincipalURL": dav_url,
        "PayloadDisplayName": "Contacts",
        "PayloadIdentifier": f"{ident}.mailstack.carddav.{uuid.uuid5(uuid.NAMESPACE_DNS, who.username)}",
        "PayloadType": "com.apple.carddav.account",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadVersion": 1,
    }
    profile = {
        "PayloadContent": [account, caldav, carddav],
        "PayloadDisplayName": f"{who.username} ({host})",
        "PayloadIdentifier": f"{ident}.mailstack.{uuid.uuid5(uuid.NAMESPACE_DNS, who.username)}",
        "PayloadRemovalDisallowed": False,
        "PayloadType": "Configuration",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadVersion": 1,
    }
    filename = who.username.replace("@", "_at_") + ".mobileconfig"
    return Response(
        plistlib.dumps(profile),
        media_type="application/x-apple-aspen-config",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
