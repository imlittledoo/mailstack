import time

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db, services
from ..config import settings
from .deps import Principal, require_admin, require_superadmin

router = APIRouter(tags=["system"])


_stat_cache: tuple[float, dict | None] = (0.0, None)


def _rspamd_stat() -> dict | None:
    # /stat also polls the public fuzzy servers and can take seconds; cache it briefly.
    global _stat_cache
    fetched_at, cached = _stat_cache
    if cached is not None and time.monotonic() - fetched_at < 30:
        return cached
    try:
        stat = services.rspamd("/stat")
    except services.ServiceError:
        return None
    result = {
        "scanned": stat.get("scanned", 0),
        "learned": stat.get("learned", 0),
        "spam_count": stat.get("spam_count", 0),
        "ham_count": stat.get("ham_count", 0),
        "actions": stat.get("actions", {}),
        "uptime": stat.get("uptime"),
    }
    _stat_cache = (time.monotonic(), result)
    return result


@router.get("/overview")
def overview(who: Principal = Depends(require_admin)):
    cond, args = who.scope("d.name")
    counts = db.query_one(
        f"""SELECT
             (SELECT COUNT(*) FROM domains d WHERE {cond}) AS domains,
             (SELECT COUNT(*) FROM mailboxes m JOIN domains d ON d.id=m.domain_id WHERE {cond}) AS mailboxes,
             (SELECT COUNT(*) FROM aliases a JOIN domains d ON d.id=a.domain_id WHERE {cond}) AS aliases,
             (SELECT COUNT(*) FROM alias_domains ad JOIN domains d ON d.id=ad.target_domain_id WHERE {cond})
               AS alias_domains,
             (SELECT COALESCE(SUM(m.quota_mb), 0) FROM mailboxes m JOIN domains d ON d.id=m.domain_id
               WHERE {cond}) AS quota_allocated_mb""",
        args * 5,
    )
    return {
        "mail_hostname": settings.mail_hostname,
        "counts": counts,
        "services": services.status(),
    }


@router.get("/status")
def status(_: Principal = Depends(require_admin)):
    return services.status()


@router.get("/spam/stats")
def spam_stats(_: Principal = Depends(require_superadmin)):
    stat = _rspamd_stat()
    if stat is None:
        raise HTTPException(status_code=502, detail="Rspamd is not reachable")
    return stat


@router.get("/spam/history")
def spam_history(limit: int = Query(100, ge=1, le=1000), who: Principal = Depends(require_admin)):
    """Recent scans. Domain admins only see messages from or to their domains."""
    try:
        data = services.rspamd("/history", {"from": 0, "to": limit - 1})
    except services.ServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    rows = data.get("rows", []) if isinstance(data, dict) else data
    out = []
    for r in rows[:limit]:
        rcpt = r.get("rcpt_mime") or r.get("rcpt_smtp") or []
        rcpt = rcpt if isinstance(rcpt, list) else [rcpt]
        sender = r.get("sender_mime") or r.get("sender_smtp")
        if who.domains is not None and not any(
            who.can(_domain_of(a)) for a in [*rcpt, sender] if a
        ):
            continue
        out.append({
            "time": r.get("unix_time"),
            "ip": r.get("ip"),
            "sender": sender,
            "rcpt": rcpt,
            "subject": r.get("subject"),
            "action": r.get("action"),
            "score": r.get("score"),
            "required_score": r.get("required_score"),
            "size": r.get("size"),
            "user": r.get("user"),
            "message_id": r.get("message-id"),
        })
    return out


@router.get("/audit")
def audit_log(limit: int = Query(200, ge=1, le=2000), who: Principal = Depends(require_admin)):
    """Domain admins see entries that concern their domains, plus their own actions."""
    if who.domains is None:
        return db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT %s", (limit,))
    names = sorted(who.domains)
    if not names:
        return db.query("SELECT * FROM audit_log WHERE actor=%s ORDER BY id DESC LIMIT %s", (who.username, limit))
    return db.query(
        """SELECT * FROM audit_log
           WHERE actor=%s OR target IN %s OR SUBSTRING_INDEX(target, '@', -1) IN %s
           ORDER BY id DESC LIMIT %s""",
        (who.username, names, names, limit),
    )


def _domain_of(address: str) -> str:
    return address.rpartition("@")[2].strip("<> ").lower()
