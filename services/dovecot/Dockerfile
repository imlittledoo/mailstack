FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# Dovecot 2.3 (bookworm). Debian trixie ships 2.4, which uses a different config syntax.
RUN groupadd -g 5000 vmail \
 && useradd -u 5000 -g 5000 -d /var/vmail -M -s /usr/sbin/nologin vmail \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      dovecot-core dovecot-imapd dovecot-pop3d dovecot-lmtpd dovecot-mysql \
      dovecot-sieve dovecot-managesieved curl ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/* /etc/dovecot/conf.d /etc/dovecot/*.ext /etc/dovecot/private \
 && mkdir -p /var/vmail && chown vmail:vmail /var/vmail

COPY conf/ /etc/dovecot/
COPY sieve-pipe/ /usr/local/lib/dovecot/sieve-pipe/
COPY entrypoint.sh /entrypoint.sh
COPY cert-watch.sh /usr/local/bin/cert-watch
COPY purge-orphans.sh /usr/local/bin/purge-orphans
RUN chmod +x /entrypoint.sh /usr/local/bin/cert-watch /usr/local/bin/purge-orphans /usr/local/lib/dovecot/sieve-pipe/*

EXPOSE 24 110 143 993 995 4190 12340 12345

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD bash -c '</dev/tcp/127.0.0.1/24 && </dev/tcp/127.0.0.1/993' || exit 1

ENTRYPOINT ["/entrypoint.sh"]
