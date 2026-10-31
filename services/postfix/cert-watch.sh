#!/bin/sh
# cert-watch <reload command...>: run the command whenever the TLS certificate changes.
CERT=/etc/ssl/mail/cert.pem
last=$(sha256sum "$CERT" 2>/dev/null | cut -d' ' -f1)
while sleep 60; do
  cur=$(sha256sum "$CERT" 2>/dev/null | cut -d' ' -f1)
  if [ -n "$cur" ] && [ "$cur" != "$last" ]; then
    last="$cur"
    echo "[cert-watch] certificate changed, reloading"
    "$@" || echo "[cert-watch] reload failed"
  fi
done
