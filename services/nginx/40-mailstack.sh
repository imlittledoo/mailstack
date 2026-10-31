#!/bin/sh
# Wait for the TLS certificate, then reload nginx whenever it is renewed.
set -eu
CERT=/etc/ssl/mail/cert.pem

i=0
until [ -s "$CERT" ] && [ -s /etc/ssl/mail/key.pem ]; do
  i=$((i + 1)); [ "$i" -gt 120 ] && { echo "[nginx] no TLS certificate available" >&2; exit 1; }
  sleep 1
done

(
  last=$(sha256sum "$CERT" | cut -d' ' -f1)
  while sleep 60; do
    cur=$(sha256sum "$CERT" 2>/dev/null | cut -d' ' -f1)
    if [ -n "$cur" ] && [ "$cur" != "$last" ]; then
      last="$cur"
      echo "[cert-watch] certificate changed, reloading nginx"
      nginx -s reload || true
    fi
  done
) &
