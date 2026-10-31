"""Recommended DNS records for a domain, verified against live DNS."""

import hashlib
import ipaddress
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import dns.exception
import dns.resolver
import dns.reversename

from . import dkim
from .config import settings


def mta_sts_policy() -> str:
    return (
        "version: STSv1\n"
        f"mode: {settings.mta_sts_mode}\n"
        f"mx: {settings.mail_hostname}\n"
        "max_age: 604800\n"
    )


def mta_sts_id() -> str:
    return hashlib.sha256(mta_sts_policy().encode()).hexdigest()[:16]


@dataclass
class Record:
    type: str
    name: str
    value: str
    purpose: str
    required: bool = True
    check: Callable[[list[str]], bool] | None = None
    lookup_type: str | None = None
    status: str = "unknown"
    current: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "name": self.name,
            "value": self.value,
            "purpose": self.purpose,
            "required": self.required,
            "status": self.status,
            "current": self.current,
            "error": self.error,
        }


def _resolver() -> dns.resolver.Resolver:
    r = dns.resolver.Resolver()
    r.timeout = 2
    r.lifetime = 4
    return r


def _host(name) -> str:
    return name.to_text().rstrip(".").lower()


def _lookup(name: str, rtype: str) -> list[str]:
    try:
        answer = _resolver().resolve(name, rtype)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    values = []
    for rr in answer:
        if rtype == "TXT":
            values.append(b"".join(rr.strings).decode(errors="replace"))
        elif rtype == "MX":
            values.append(f"{rr.preference} {_host(rr.exchange)}")
        elif rtype == "SRV":
            values.append(f"{rr.priority} {rr.weight} {rr.port} {_host(rr.target)}")
        elif rtype in ("CNAME", "PTR"):
            values.append(_host(rr.target))
        else:
            values.append(rr.to_text())
    return values


def _run(rec: Record) -> None:
    rtype = rec.lookup_type or rec.type
    try:
        rec.current = _lookup(rec.name, rtype)
    except dns.exception.DNSException as exc:
        rec.status, rec.error = "error", str(exc) or exc.__class__.__name__
        return
    if not rec.current:
        rec.status = "missing"
    elif rec.check is None or rec.check(rec.current):
        rec.status = "ok"
    else:
        rec.status = "mismatch"


def _norm(txt: str) -> str:
    return re.sub(r"\s+", "", txt).lower()


def _dkim_p(txt: str) -> str | None:
    match = re.search(r"(?:^|;)p=([^;]+)", re.sub(r"\s+", "", txt))
    return match.group(1) if match else None


def records(domain: str, dkim_row: dict | None) -> list[dict]:
    host = settings.mail_hostname
    v4, v6 = settings.public_ipv4, settings.public_ipv6

    def points_to_host(values: list[str]) -> bool:
        # A CNAME to the mail host, or A records equal to the host's.
        if host in values:
            return True
        try:
            host_ips = set(_lookup(host, "A"))
        except dns.exception.DNSException:
            return False
        return bool(host_ips) and set(values) <= host_ips

    def srv_ok(port: int) -> Callable[[list[str]], bool]:
        return lambda values: any(v.split()[2:] == [str(port), host] for v in values)

    def spf_ok(values: list[str]) -> bool:
        spf = [v for v in values if v.lower().startswith("v=spf1")]
        if len(spf) != 1:
            return False
        s = " " + spf[0].lower() + " "
        return " mx " in s or " mx:" in s or f"a:{host}" in s or (v4 and f"ip4:{v4}" in s) or "include:" in s

    def dkim_ok(values: list[str]) -> bool:
        return any(_dkim_p(v) == dkim_row["public_key"] for v in values)

    recs = [
        Record("MX", domain, f"10 {host}", "Routes incoming mail for the domain to this server",
               check=lambda vals: any(v.split()[-1] == host for v in vals)),
        Record("TXT", domain, "v=spf1 mx -all", "SPF: authorizes this server to send for the domain",
               check=spf_ok),
    ]
    if dkim_row:
        recs.append(Record("TXT", f"{dkim_row['selector']}._domainkey.{domain}", dkim.txt_value(dkim_row["public_key"]),
                           "DKIM public key (outgoing mail is signed with it)", check=dkim_ok))
    recs += [
        Record("TXT", f"_dmarc.{domain}", f"v=DMARC1; p=quarantine; rua=mailto:postmaster@{domain}",
               "DMARC policy for failed SPF/DKIM", check=lambda vals: any(v.upper().startswith("V=DMARC1") for v in vals)),
        Record("CNAME", f"autoconfig.{domain}", host, "Thunderbird / generic client autoconfiguration",
               required=False, lookup_type="A", check=points_to_host),
        Record("CNAME", f"autodiscover.{domain}", host, "Outlook autodiscover",
               required=False, lookup_type="A", check=points_to_host),
        Record("SRV", f"_autodiscover._tcp.{domain}", f"0 1 443 {host}", "Outlook autodiscover (SRV)",
               required=False, check=srv_ok(443)),
        Record("SRV", f"_imaps._tcp.{domain}", f"0 1 993 {host}", "IMAP over TLS (RFC 6186)",
               required=False, check=srv_ok(993)),
        Record("SRV", f"_submissions._tcp.{domain}", f"0 1 465 {host}", "SMTP submission over TLS (RFC 8314)",
               required=False, check=srv_ok(465)),
        Record("SRV", f"_submission._tcp.{domain}", f"0 1 587 {host}", "SMTP submission with STARTTLS",
               required=False, check=srv_ok(587)),
        Record("SRV", f"_pop3s._tcp.{domain}", f"0 1 995 {host}", "POP3 over TLS",
               required=False, check=srv_ok(995)),
        Record("SRV", f"_sieve._tcp.{domain}", f"0 1 4190 {host}", "ManageSieve (filter rules)",
               required=False, check=srv_ok(4190)),
        Record("SRV", f"_caldavs._tcp.{domain}", f"0 1 443 {host}", "Calendar discovery (CalDAV)",
               required=False, check=srv_ok(443)),
        Record("SRV", f"_carddavs._tcp.{domain}", f"0 1 443 {host}", "Contacts discovery (CardDAV)",
               required=False, check=srv_ok(443)),
        Record("CNAME", f"mta-sts.{domain}", host, "MTA-STS policy host (add it to ADDITIONAL_SAN for a valid certificate)",
               required=False, lookup_type="A", check=points_to_host),
        Record("TXT", f"_mta-sts.{domain}", f"v=STSv1; id={mta_sts_id()}", "MTA-STS: require TLS for inbound mail",
               required=False, check=lambda vals: any(_norm(v) == _norm(f"v=STSv1; id={mta_sts_id()}") for v in vals)),
        Record("TXT", f"_smtp._tls.{domain}", f"v=TLSRPTv1; rua=mailto:postmaster@{domain}", "TLS failure reports",
               required=False, check=lambda vals: any(v.upper().startswith("V=TLSRPTV1") for v in vals)),
        Record("A", host, v4 or "<public IPv4 of this server>", "Address of the mail server",
               check=(lambda vals: v4 in vals) if v4 else None),
    ]
    if v6:
        recs.append(Record("AAAA", host, v6, "IPv6 address of the mail server",
                           check=lambda vals: any(ipaddress.ip_address(x) == ipaddress.ip_address(v6) for x in vals)))
    for ip in filter(None, (v4, v6)):
        recs.append(Record("PTR", dns.reversename.from_address(ip).to_text().rstrip("."), host,
                           "Reverse DNS - set at your hosting provider; many receivers reject mail without it",
                           check=lambda vals: host in vals))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_run, recs))
    return [r.as_dict() for r in recs]
