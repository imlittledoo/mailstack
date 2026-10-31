# MailStack

A complete, self-hosted mail server stack in Docker: an alternative to mailcow, built from its own images, configuration and admin panel.

| Service   | What it does |
|-----------|--------------|
| `postfix` | SMTP (25), submission (587, STARTTLS), SMTPS (465). Virtual domains live in MariaDB. |
| `dovecot` | IMAP/POP3 (TLS), LMTP delivery, SASL for Postfix, quotas, Sieve/ManageSieve. |
| `rspamd`  | Spam filtering, greylisting, Bayes learning, DKIM signing. |
| `admin`   | **MailStack admin panel and REST API** (FastAPI and a dependency-free web UI). |
| `webmail` | Roundcube, served at `/webmail/`. |
| `radicale`| CalDAV/CardDAV (calendars and contacts) at `/dav/`, authenticated by the admin service. |
| `nginx`   | TLS reverse proxy for the panel, webmail, Rspamd UI, autoconfig and MTA-STS. |
| `acme`    | Let's Encrypt certificates (HTTP-01). Every service reloads automatically on renewal. |
| `unbound` | Private DNSSEC-validating resolver. DNS blocklists need it because they refuse public resolvers. |
| `mariadb`, `redis` | Storage. |
| `clamav`  | Optional virus scanning (`COMPOSE_PROFILES=clamav`). |

## Features

- **Two-factor login (TOTP)** for admins and mailbox users:
  - QR-code setup and 10 single-use recovery codes
  - a code can't be used twice
  - turning it on or off, or changing your password, signs out your other sessions
  - superadmins can reset it for anyone
- **Superadmins and domain admins.** Domain admins only see and manage their assigned domains (and those domains' alias domains): mailboxes, aliases, DKIM, DNS, filtered spam history and audit log.
- **App passwords**: separate, revocable passwords per device for IMAP, SMTP and CalDAV/CardDAV. They can't sign in to the panel.
- **Calendars and contacts** (CalDAV/CardDAV). These are included in the iPhone/Mac profile and found through `/.well-known/caldav` and `/.well-known/carddav`.
- Domains with limits: mailbox count, alias count, per-mailbox and total quota
- Mailboxes with quotas and live usage (read through the Dovecot doveadm API)
- Aliases with multiple targets, catch-all aliases (`@domain`) and alias domains
- DKIM keys created per domain, rotated with one click, and picked up by Rspamd without a restart
- A **DNS checker** that lists every record a domain needs (MX, SPF, DKIM, DMARC, autoconfig, SRV, MTA-STS, TLS-RPT, A/PTR) and tests each against live DNS
- Spam handling:
  - Spam is filed into `Junk` automatically.
  - Moving a message into or out of `Junk` trains Rspamd (IMAPSieve).
  - Junk is purged after 30 days.
- Postfix rejects mail for a mailbox that is over quota at RCPT time, so the sender gets an error instead of a later bounce.
- Sender protection: a login may only send as its own address or aliases it owns
- Self-service portal for mailbox users:
  - quota usage
  - aliases pointing at the mailbox
  - password change
  - client settings
  - app passwords
  - two-factor settings
  - an iOS/macOS profile download (mail, calendar and contacts)
- Client autoconfiguration for Thunderbird (`autoconfig`) and Outlook (`autodiscover`)
- Audit log of every admin action and login, with brute-force throttling on login
- REST API with OpenAPI docs at `/api/docs`; authenticate with the `X-API-Key` header
- A `mailstack.sh` helper script for setup, backup/restore, the mail queue and maintenance

## Requirements

- A Linux server (2 GB RAM, or 3 GB+ with ClamAV) with Docker Engine and the Compose plugin
- A hostname such as `mail.example.com` with an **A record** pointing at the server, and a **PTR (reverse DNS)** record pointing back to it. Your hosting provider sets the PTR.
- Outbound port 25 open (many cloud providers block it by default)
- Inbound ports 25, 465, 587, 143, 993, 110, 995, 4190, 80, 443

## Quick start

```bash
git clone <this repo> mailstack && cd mailstack
./mailstack.sh init mail.example.com you@example.com   # writes .env with random secrets and prints the admin password
./mailstack.sh up
```

Then:

1. Open `https://mail.example.com/` and sign in as `admin`.
2. **Domains → Add domain.** A DKIM key is generated automatically.
3. Click **DNS** on the domain and create the records it lists. Re-check until every required record is `ok`.
4. **Mailboxes → Add mailbox.** Users can sign in to the same panel with their email address, or use `/webmail/`.

The certificate starts out self-signed. The `acme` container replaces it with a Let's Encrypt certificate once `MAIL_HOSTNAME` resolves to the server and port 80 is reachable. Run `./mailstack.sh logs acme` to watch progress.

## Configuration

Everything is set in `.env`; see `.env.example` for all options. Common ones:

| Variable | Purpose |
|----------|---------|
| `ADDITIONAL_SAN` | Extra certificate names, e.g. `autodiscover.example.com,mta-sts.example.com` |
| `COMPOSE_PROFILES=clamav` | Turn on virus scanning |
| `RELAYHOST` / `RELAYHOST_USER` / `RELAYHOST_PASSWORD` | Send outbound mail through a smarthost when port 25 is blocked |
| `MTA_STS_MODE` | `testing` (default) or `enforce` once TLS is working |
| `PUBLIC_IPV4` / `PUBLIC_IPV6` | Lets the DNS checker verify A/AAAA/PTR records |
| `HTTP_PORT=127.0.0.1:8080` | Bind a port to one address, e.g. behind another reverse proxy |

Run `./mailstack.sh up` again after changing `.env`.

## Day-to-day operations

```bash
./mailstack.sh ps                       # status
./mailstack.sh logs postfix             # follow logs of one service
./mailstack.sh queue | flush            # inspect / retry the mail queue
./mailstack.sh backup                   # DB + mail + DKIM + certs -> ./backups/<timestamp>
./mailstack.sh restore backups/<ts>     # restore a backup
./mailstack.sh update                   # rebuild with fresh base images
./mailstack.sh reset-admin admin        # forgot the admin password
./mailstack.sh purge-orphans [--delete] # remove mail of deleted mailboxes
./mailstack.sh doveadm quota get -u jane@example.com
```

Deleting a mailbox in the panel removes it right away but keeps its stored mail on disk until you run `purge-orphans --delete`.

## Security notes

- **Client IPs.** Docker's userland proxy hides real client IPs, so every remote connection appears to come from the Docker gateway. MailStack never trusts private ranges, so this cannot create an open relay. But spam checks and logs work much better with `"userland-proxy": false` in `/etc/docker/daemon.json`, followed by a Docker restart.
- **Internal ports.** Postfix SASL, LMTP, doveadm, the Rspamd controller, MariaDB and Redis are only reachable on the internal Docker network.
- **Admin panel sessions.** Sessions are signed, `Secure`, `SameSite=Strict` cookies. Mutating API calls need a custom header (CSRF protection), and changing a password ends every existing session for that account.
- **Passwords** are stored as bcrypt (`{BLF-CRYPT}`) and verified by both Dovecot and the panel. App passwords are random 100-bit strings stored as SHA-256, so Dovecot can match them directly in SQL.
- **Two-factor scope.** Two-factor protects the admin panel. IMAP, SMTP, DAV and webmail still accept the mailbox password, because mail protocols have no second factor. For devices, use app passwords: you can revoke one without changing the main password.
- **Calendar/contacts auth.** nginx checks every `/dav/` request against the admin service and overwrites `X-Remote-User`, so clients can't impersonate another user. Successful checks are cached for 2 minutes.

## Layout

```
docker-compose.yml        the stack
mailstack.sh              management helper
services/
  admin/                  FastAPI app (app/api/*), schema migrations (app/db.py), web UI (app/static)
  postfix/ dovecot/       configs and entrypoints (the SQL lookups live in the entrypoints)
  rspamd/                 local.d overrides
  nginx/ acme/ unbound/ webmail/
```

The `admin` service owns the database schema. When you change tables in `services/admin/app/db.py`, also update the SQL queries in the Postfix and Dovecot entrypoints.
