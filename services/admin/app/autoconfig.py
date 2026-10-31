"""Mail client autoconfiguration (Thunderbird, Outlook) and the MTA-STS policy."""

import re
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from . import db, dnscheck, validation
from .config import settings

router = APIRouter(include_in_schema=False)


def _known_domain(name: str) -> bool:
    return db.query_one(
        """SELECT 1 AS ok FROM domains WHERE name=%s AND active=1
           UNION SELECT 1 FROM alias_domains WHERE alias_domain=%s AND active=1""",
        (name, name),
    ) is not None


def _domain_from(email: str, host: str, prefix: str) -> str:
    if "@" in email:
        candidate = email.rpartition("@")[2]
    else:
        candidate = host.split(":")[0].lower()
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
    try:
        name = validation.domain(candidate)
    except HTTPException:
        raise HTTPException(status_code=404)
    if not _known_domain(name):
        raise HTTPException(status_code=404)
    return name


@router.get("/mail/config-v1.1.xml")
@router.get("/.well-known/autoconfig/mail/config-v1.1.xml")
def thunderbird(request: Request, emailaddress: str = ""):
    domain = escape(_domain_from(emailaddress, request.headers.get("host", ""), "autoconfig."))
    h = escape(settings.mail_hostname)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<clientConfig version="1.1">
  <emailProvider id="{domain}">
    <domain>{domain}</domain>
    <displayName>{domain}</displayName>
    <displayShortName>{domain}</displayShortName>
    <incomingServer type="imap">
      <hostname>{h}</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
      <authentication>password-cleartext</authentication>
      <username>%EMAILADDRESS%</username>
    </incomingServer>
    <incomingServer type="pop3">
      <hostname>{h}</hostname>
      <port>995</port>
      <socketType>SSL</socketType>
      <authentication>password-cleartext</authentication>
      <username>%EMAILADDRESS%</username>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>{h}</hostname>
      <port>465</port>
      <socketType>SSL</socketType>
      <authentication>password-cleartext</authentication>
      <username>%EMAILADDRESS%</username>
    </outgoingServer>
    <outgoingServer type="smtp">
      <hostname>{h}</hostname>
      <port>587</port>
      <socketType>STARTTLS</socketType>
      <authentication>password-cleartext</authentication>
      <username>%EMAILADDRESS%</username>
    </outgoingServer>
  </emailProvider>
</clientConfig>
"""
    return Response(xml, media_type="application/xml")


@router.post("/autodiscover/autodiscover.xml")
@router.post("/Autodiscover/Autodiscover.xml")
async def outlook(request: Request):
    # Regex rather than an XML parser: the body is untrusted and we need one value.
    body = (await request.body())[:16384].decode("utf-8", errors="ignore")
    match = re.search(r"<EMailAddress>\s*([^<\s]+)\s*</EMailAddress>", body, re.IGNORECASE)
    if not match:
        raise HTTPException(status_code=400)
    email = match.group(1).strip().lower()
    _domain_from(email, "", "")
    e, h = escape(email), escape(settings.mail_hostname)
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<Autodiscover xmlns="http://schemas.microsoft.com/exchange/autodiscover/responseschema/2006">
  <Response xmlns="http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a">
    <Account>
      <AccountType>email</AccountType>
      <Action>settings</Action>
      <Protocol>
        <Type>IMAP</Type>
        <Server>{h}</Server>
        <Port>993</Port>
        <DomainRequired>off</DomainRequired>
        <LoginName>{e}</LoginName>
        <SPA>off</SPA>
        <SSL>on</SSL>
        <AuthRequired>on</AuthRequired>
      </Protocol>
      <Protocol>
        <Type>SMTP</Type>
        <Server>{h}</Server>
        <Port>465</Port>
        <DomainRequired>off</DomainRequired>
        <LoginName>{e}</LoginName>
        <SPA>off</SPA>
        <Encryption>SSL</Encryption>
        <AuthRequired>on</AuthRequired>
        <UsePOPAuth>on</UsePOPAuth>
        <SMTPLast>off</SMTPLast>
      </Protocol>
    </Account>
  </Response>
</Autodiscover>
"""
    return Response(xml, media_type="application/xml")


@router.get("/.well-known/mta-sts.txt")
def mta_sts():
    return PlainTextResponse(dnscheck.mta_sts_policy())
