#!/bin/sh
# Certificate manager: guarantees a certificate exists (self-signed bootstrap),
# then obtains/renews a Let's Encrypt certificate via HTTP-01 through nginx.
# Consumers (nginx, postfix, dovecot) watch cert.pem and reload on change.
set -u

CERT_DIR=/etc/ssl/mail
WEBROOT=/var/www/acme
CHALLENGE_DIR="$WEBROOT/.well-known/acme-challenge"
LE_NAME=mailstack

log() { echo "[acme] $*"; }

: "${MAIL_HOSTNAME:?MAIL_HOSTNAME is required}"
mkdir -p "$CERT_DIR" "$CHALLENGE_DIR"

DOMAINS="$MAIL_HOSTNAME"
for san in $(echo "${ADDITIONAL_SAN:-}" | tr ',' ' '); do
  [ "$san" != "$MAIL_HOSTNAME" ] && DOMAINS="$DOMAINS $san"
done

is_yes() { case "${1:-}" in y|Y|yes|YES|true|1) return 0 ;; *) return 1 ;; esac; }

# install_cert <fullchain> <key>: atomically replace the live certificate if it changed.
install_cert() {
  if [ -s "$CERT_DIR/cert.pem" ] && cmp -s "$1" "$CERT_DIR/cert.pem"; then
    return 0
  fi
  cp "$2" "$CERT_DIR/key.pem.new" && chmod 600 "$CERT_DIR/key.pem.new" && mv -f "$CERT_DIR/key.pem.new" "$CERT_DIR/key.pem"
  cp "$1" "$CERT_DIR/cert.pem.new" && chmod 644 "$CERT_DIR/cert.pem.new" && mv -f "$CERT_DIR/cert.pem.new" "$CERT_DIR/cert.pem"
  log "installed certificate: $(openssl x509 -in "$CERT_DIR/cert.pem" -noout -subject -enddate | tr '\n' ' ')"
}

self_signed() {
  san=""
  for d in $DOMAINS; do san="${san:+$san,}DNS:$d"; done
  tmp=$(mktemp -d)
  openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
    -subj "/CN=$MAIL_HOSTNAME" -addext "subjectAltName=$san" \
    -keyout "$tmp/key.pem" -out "$tmp/cert.pem" 2>/dev/null
  install_cert "$tmp/cert.pem" "$tmp/key.pem"
  rm -rf "$tmp"
}

if [ ! -s "$CERT_DIR/cert.pem" ] || [ ! -s "$CERT_DIR/key.pem" ]; then
  log "no certificate found, creating a self-signed bootstrap certificate"
  self_signed
fi

if is_yes "${SKIP_LETSENCRYPT:-n}"; then
  log "SKIP_LETSENCRYPT=y - keeping the current certificate. Place your own cert.pem/key.pem in the 'certs' volume to replace it."
  while true; do sleep 86400; done
fi

set -- --webroot -w "$WEBROOT" --cert-name "$LE_NAME" --non-interactive --agree-tos --keep-until-expiring --expand
for d in $DOMAINS; do set -- "$@" -d "$d"; done
if [ -n "${ACME_EMAIL:-}" ]; then set -- "$@" --email "$ACME_EMAIL"; else set -- "$@" --register-unsafely-without-email; fi
is_yes "${ACME_STAGING:-n}" && set -- "$@" --staging

wait_for_nginx() {
  token="probe-$$-$(date +%s)"
  echo ok > "$CHALLENGE_DIR/$token"
  i=0
  until curl -fsS -o /dev/null "http://nginx/.well-known/acme-challenge/$token" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then log "nginx is not serving the challenge directory yet, trying anyway"; break; fi
    sleep 5
  done
  rm -f "$CHALLENGE_DIR/$token"
}

while true; do
  wait_for_nginx
  log "requesting/renewing certificate for: $DOMAINS"
  if certbot certonly "$@"; then
    install_cert "/etc/letsencrypt/live/$LE_NAME/fullchain.pem" "/etc/letsencrypt/live/$LE_NAME/privkey.pem"
    sleep 43200
  else
    log "certificate request failed (DNS for $MAIL_HOSTNAME must point here and port 80 must be reachable). Retrying in 1 hour."
    sleep 3600
  fi
done
