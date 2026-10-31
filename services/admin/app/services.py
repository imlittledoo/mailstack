"""Clients for the other containers: Dovecot doveadm API, Rspamd controller, health probes."""

import base64
import logging
import socket
from concurrent.futures import ThreadPoolExecutor

import httpx

from .config import settings

log = logging.getLogger("mailstack.services")


class ServiceError(Exception):
    pass


def doveadm(commands: list[list]) -> list[list]:
    """Run doveadm commands over HTTP: [[command, params, tag], ...]."""
    token = base64.b64encode(settings.doveadm_api_key.encode()).decode()
    try:
        resp = httpx.post(
            settings.doveadm_url,
            json=commands,
            headers={"Authorization": f"X-Dovecot-API {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ServiceError(f"doveadm: {exc}") from exc


def quota_usage(usernames: list[str]) -> dict[str, dict]:
    """Return {username: {used_bytes, limit_bytes, messages}}; empty if Dovecot is unreachable."""
    if not usernames:
        return {}
    commands = [["quotaGet", {"user": u}, str(i)] for i, u in enumerate(usernames)]
    try:
        responses = doveadm(commands)
    except ServiceError as exc:
        log.warning("quota lookup failed: %s", exc)
        return {}
    usage = {}
    for kind, payload, tag in responses:
        if kind != "doveadmResponse":
            continue
        user = usernames[int(tag)]
        entry = {"used_bytes": 0, "limit_bytes": None, "messages": 0}
        for row in payload:
            if row.get("type") == "STORAGE":
                entry["used_bytes"] = int(row.get("value") or 0) * 1024
                limit = row.get("limit")
                entry["limit_bytes"] = int(limit) * 1024 if limit not in (None, "", "-") else None
            elif row.get("type") == "MESSAGE":
                entry["messages"] = int(row.get("value") or 0)
        usage[user] = entry
    return usage


def rspamd(path: str, params: dict | None = None) -> dict | list:
    try:
        resp = httpx.get(
            settings.rspamd_url + path,
            params=params,
            headers={"Password": settings.rspamd_password},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ServiceError(f"rspamd: {exc}") from exc


def _tcp(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _http(url: str) -> bool:
    try:
        return httpx.get(url, timeout=3).status_code < 500
    except httpx.HTTPError:
        return False


CHECKS = [
    ("postfix", "SMTP (25)", lambda: _tcp("postfix", 25)),
    ("postfix", "Submission (587)", lambda: _tcp("postfix", 587)),
    ("dovecot", "IMAPS (993)", lambda: _tcp("dovecot", 993)),
    ("dovecot", "LMTP (24)", lambda: _tcp("dovecot", 24)),
    ("rspamd", "Controller", lambda: _http(settings.rspamd_url + "/ping")),
    ("rspamd", "Milter (11332)", lambda: _tcp("rspamd", 11332)),
    ("redis", "Redis", lambda: _tcp("redis", 6379)),
    ("mariadb", "MariaDB", lambda: _tcp(settings.db_host, 3306)),
    ("unbound", "DNS resolver", lambda: _tcp("unbound", 53)),
    ("webmail", "Roundcube", lambda: _http("http://webmail/")),
    ("radicale", "CalDAV / CardDAV", lambda: _tcp("radicale", 5232)),
    ("nginx", "Web (443)", lambda: _tcp("nginx", 443)),
]


def status() -> list[dict]:
    checks = list(CHECKS)
    if settings.clamav_enabled:
        checks.append(("clamav", "ClamAV", lambda: _tcp("clamav", 3310)))
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        results = list(pool.map(lambda c: c[2](), checks))
    return [
        {"service": svc, "check": name, "ok": ok}
        for (svc, name, _), ok in zip(checks, results)
    ]
