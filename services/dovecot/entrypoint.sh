#!/bin/sh
set -eu

: "${MAIL_HOSTNAME:?MAIL_HOSTNAME is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${DOVEADM_API_KEY:?DOVEADM_API_KEY is required}"
: "${RSPAMD_PASSWORD:?RSPAMD_PASSWORD is required}"
DB_HOST="${DB_HOST:-mariadb}"
DB_NAME="${DB_NAME:-mailstack}"
DB_USER="${DB_USER:-mailstack}"

i=0
until [ -s /etc/ssl/mail/cert.pem ] && [ -s /etc/ssl/mail/key.pem ]; do
  i=$((i + 1)); [ "$i" -gt 120 ] && { echo "[dovecot] no TLS certificate available" >&2; exit 1; }
  sleep 1
done

cat > /etc/dovecot/dovecot-sql.conf.ext <<EOF
driver = mysql
connect = host=$DB_HOST dbname=$DB_NAME user=$DB_USER password=$DB_PASSWORD
default_pass_scheme = BLF-CRYPT

password_query = SELECT m.username AS user, m.password_hash AS password \\
  FROM mailboxes m JOIN domains d ON d.id = m.domain_id \\
  WHERE m.username = '%u' AND m.active = 1 AND d.active = 1

user_query = SELECT CONCAT('/var/vmail/', d.name, '/', m.local_part) AS home, \\
  5000 AS uid, 5000 AS gid, CONCAT('*:bytes=', m.quota_mb * 1048576) AS quota_rule \\
  FROM mailboxes m JOIN domains d ON d.id = m.domain_id \\
  WHERE m.username = '%u' AND m.active = 1 AND d.active = 1

iterate_query = SELECT m.username AS user FROM mailboxes m JOIN domains d ON d.id = m.domain_id \\
  WHERE m.active = 1 AND d.active = 1
EOF
chmod 600 /etc/dovecot/dovecot-sql.conf.ext

cat > /etc/dovecot/dovecot-sql-app.conf.ext <<EOF
driver = mysql
connect = host=$DB_HOST dbname=$DB_NAME user=$DB_USER password=$DB_PASSWORD

password_query = SELECT m.username AS user, NULL AS password, 'Y' AS nopassword \\
  FROM app_passwords ap JOIN mailboxes m ON m.id = ap.mailbox_id JOIN domains d ON d.id = m.domain_id \\
  WHERE m.username = '%u' AND ap.secret_sha256 = SHA2('%w', 256) AND m.active = 1 AND d.active = 1
EOF
chmod 600 /etc/dovecot/dovecot-sql-app.conf.ext

cat > /etc/dovecot/secrets.conf <<EOF
hostname = $MAIL_HOSTNAME
doveadm_api_key = $DOVEADM_API_KEY
EOF
chmod 600 /etc/dovecot/secrets.conf

printf '%s' "$RSPAMD_PASSWORD" > /etc/dovecot/rspamd-password
chown root:vmail /etc/dovecot/rspamd-password
chmod 640 /etc/dovecot/rspamd-password

chown vmail:vmail /var/vmail

# Pre-compile global sieve scripts (Dovecot cannot write to /etc at runtime).
sievec /etc/dovecot/sieve/before.d
for script in /etc/dovecot/sieve/*.sieve; do sievec "$script"; done

cert-watch doveadm reload &

exec dovecot -F
