import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from .. import db
from ..config import settings
from ..security import fingerprint

SUPERADMIN = "superadmin"
DOMAINADMIN = "domainadmin"


@dataclass
class Principal:
    role: str  # "admin" | "user"
    username: str
    ip: str = ""
    superadmin: bool = False
    # Domains (incl. alias domains) a domain admin may manage; None = everything.
    domains: frozenset[str] | None = None
    via_api_key: bool = False

    def can(self, domain: str) -> bool:
        return self.domains is None or domain in self.domains

    def scope(self, column: str) -> tuple[str, list]:
        """SQL condition limiting `column` (a domain name) to this principal's domains."""
        if self.domains is None:
            return "1=1", []
        if not self.domains:
            return "1=0", []
        return f"{column} IN %s", [sorted(self.domains)]


def client_ip(request: Request) -> str:
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "")


def admin_domains(admin_id: int) -> frozenset[str]:
    rows = db.query(
        """SELECT d.name FROM admin_domains x JOIN domains d ON d.id = x.domain_id WHERE x.admin_id = %s
           UNION SELECT ad.alias_domain FROM admin_domains x
             JOIN alias_domains ad ON ad.target_domain_id = x.domain_id WHERE x.admin_id = %s""",
        (admin_id, admin_id),
    )
    return frozenset(r["name"] for r in rows)


def account_row(role: str, username: str) -> dict | None:
    """Active admin or mailbox row with the fields auth needs."""
    if role == "admin":
        return db.query_one(
            """SELECT id, username, role, password_hash, totp_secret, totp_last_step, recovery_codes
               FROM admins WHERE username=%s AND active=1""",
            (username,),
        )
    if role == "user":
        return db.query_one(
            """SELECT m.id, m.username, 'user' AS role, m.password_hash, m.totp_secret, m.totp_last_step,
                      m.recovery_codes
               FROM mailboxes m JOIN domains d ON d.id=m.domain_id
               WHERE m.username=%s AND m.active=1 AND d.active=1""",
            (username,),
        )
    return None


def principal_for(role: str, row: dict, ip: str) -> Principal:
    if role == "admin":
        superadmin = row["role"] == SUPERADMIN
        return Principal("admin", row["username"], ip, superadmin, None if superadmin else admin_domains(row["id"]))
    return Principal("user", row["username"], ip)


def _session_principal(request: Request) -> Principal | None:
    auth = request.session.get("auth")
    if not isinstance(auth, dict):
        return None
    row = account_row(auth.get("role", ""), auth.get("username", ""))
    if not row or not hmac.compare_digest(fingerprint(row["password_hash"], row["totp_secret"]), auth.get("fp", "")):
        request.session.clear()
        return None
    return principal_for(auth["role"], row, client_ip(request))


def get_principal(request: Request) -> Principal | None:
    key = request.headers.get("x-api-key")
    if key is not None:
        if settings.api_key and hmac.compare_digest(key, settings.api_key):
            return Principal("admin", "api", client_ip(request), superadmin=True, via_api_key=True)
        raise HTTPException(status_code=401, detail="Invalid API key")
    return _session_principal(request)


def require_auth(request: Request) -> Principal:
    principal = get_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return principal


def require_admin(principal: Principal = Depends(require_auth)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return principal


def require_superadmin(principal: Principal = Depends(require_admin)) -> Principal:
    if not principal.superadmin:
        raise HTTPException(status_code=403, detail="Only superadmins can do this")
    return principal


def require_user(principal: Principal = Depends(require_auth)) -> Principal:
    if principal.role != "user":
        raise HTTPException(status_code=403, detail="Only available to mailbox users")
    return principal


def require_session(principal: Principal = Depends(require_auth)) -> Principal:
    if principal.via_api_key:
        raise HTTPException(status_code=403, detail="Not available with an API key")
    return principal


def ensure_scope(principal: Principal, domain: str) -> None:
    if not principal.can(domain):
        raise HTTPException(status_code=403, detail=f"You do not manage {domain}")


def audit(cur, who: Principal, action: str, target: str = "", details: str = "") -> None:
    cur.execute(
        "INSERT INTO audit_log (actor, action, target, details, ip) VALUES (%s, %s, %s, %s, %s)",
        (who.username, action, target[:320], details[:1000], who.ip[:45]),
    )


def not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


def conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=message)
