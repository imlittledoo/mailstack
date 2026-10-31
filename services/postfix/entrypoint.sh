#!/bin/sh
set -eu

: "${MAIL_HOSTNAME:?MAIL_HOSTNAME is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
DB_HOST="${DB_HOST:-mariadb}"
DB_NAME="${DB_NAME:-mailstack}"
DB_USER="${DB_USER:-mailstack}"
SQL_DIR=/etc/postfix/sql

i=0
until [ -s /etc/ssl/mail/cert.pem ] && [ -s /etc/ssl/mail/key.pem ]; do
  i=$((i + 1)); [ "$i" -gt 120 ] && { echo "[postfix] no TLS certificate available" >&2; exit 1; }
  sleep 1
done

# sql_map <file> <query>: write a MySQL lookup table definition.
sql_map() {
  cat > "$SQL_DIR/$1" <<EOF
hosts = $DB_HOST
user = $DB_USER
password = $DB_PASSWORD
dbname = $DB_NAME
query = $2
EOF
}

mkdir -p "$SQL_DIR"

sql_map virtual_domains.cf "SELECT name FROM domains WHERE name='%s' AND active=1
  UNION SELECT ad.alias_domain FROM alias_domains ad JOIN domains d ON d.id=ad.target_domain_id
  WHERE ad.alias_domain='%s' AND ad.active=1 AND d.active=1"

sql_map virtual_mailboxes.cf "SELECT m.username FROM mailboxes m JOIN domains d ON d.id=m.domain_id
  WHERE m.username='%s' AND m.active=1 AND d.active=1"

sql_map virtual_alias_self.cf "SELECT m.username FROM mailboxes m JOIN domains d ON d.id=m.domain_id
  WHERE m.username='%s' AND m.active=1 AND d.active=1"

sql_map virtual_aliases.cf "SELECT a.goto FROM aliases a JOIN domains d ON d.id=a.domain_id
  WHERE a.address='%s' AND a.active=1 AND d.active=1"

sql_map alias_domain_mailboxes.cf "SELECT m.username FROM alias_domains ad
  JOIN domains d ON d.id=ad.target_domain_id
  JOIN mailboxes m ON m.domain_id=ad.target_domain_id
  WHERE ad.alias_domain='%d' AND m.local_part='%u' AND ad.active=1 AND m.active=1 AND d.active=1"

sql_map alias_domain_aliases.cf "SELECT a.goto FROM alias_domains ad
  JOIN domains d ON d.id=ad.target_domain_id
  JOIN aliases a ON a.address=CONCAT('%u', '@', d.name)
  WHERE ad.alias_domain='%d' AND ad.active=1 AND a.active=1 AND d.active=1"

# Which SASL logins may use a given envelope sender: the mailbox itself, every
# mailbox an alias delivers to, and the same mailbox through alias domains.
sql_map sender_login.cf "SELECT m.username FROM mailboxes m
  WHERE m.username='%s' AND m.active=1
  UNION SELECT m.username FROM aliases a JOIN mailboxes m ON FIND_IN_SET(m.username, a.goto) > 0
  WHERE a.address='%s' AND a.active=1 AND m.active=1
  UNION SELECT m.username FROM alias_domains ad JOIN mailboxes m ON m.domain_id=ad.target_domain_id
  WHERE ad.alias_domain='%d' AND m.local_part='%u' AND ad.active=1 AND m.active=1"

chown -R root:postfix "$SQL_DIR"
chmod 750 "$SQL_DIR"
chmod 640 "$SQL_DIR"/*.cf

postconf -e \
  "myhostname=$MAIL_HOSTNAME" \
  "message_size_limit=$(( ${MESSAGE_SIZE_LIMIT_MB:-50} * 1024 * 1024 ))"

if [ -n "${RELAYHOST:-}" ]; then
  postconf -e "relayhost=$RELAYHOST"
  if [ -n "${RELAYHOST_USER:-}" ]; then
    echo "$RELAYHOST $RELAYHOST_USER:${RELAYHOST_PASSWORD:-}" > /etc/postfix/sasl_passwd
    chmod 600 /etc/postfix/sasl_passwd
    postmap /etc/postfix/sasl_passwd
    postconf -e \
      "smtp_sasl_auth_enable=yes" \
      "smtp_sasl_password_maps=hash:/etc/postfix/sasl_passwd" \
      "smtp_sasl_security_options=noanonymous" \
      "smtp_tls_security_level=encrypt"
  fi
else
  postconf -e "relayhost="
fi

# The queue lives on a volume; make sure ownership survives image upgrades.
postfix set-permissions >/dev/null 2>&1 || true
postfix check

cert-watch postfix reload &

exec postfix start-fg
