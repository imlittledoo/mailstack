#!/bin/sh
set -eu

: "${RSPAMD_PASSWORD:?RSPAMD_PASSWORD is required}"

HASH=$(rspamadm pw -p "$RSPAMD_PASSWORD")
cat > /etc/rspamd/local.d/worker-controller.inc <<EOF
bind_socket = "*:11334";
password = "$HASH";
enable_password = "$HASH";
secure_ip = "127.0.0.1";
secure_ip = "::1";
allow_file_and_shm_inputs = false;
EOF

case ",${COMPOSE_PROFILES:-}," in
  *,clamav,*) cp /etc/rspamd/optional/antivirus.conf /etc/rspamd/local.d/antivirus.conf ;;
  *) rm -f /etc/rspamd/local.d/antivirus.conf ;;
esac

# DKIM keys are written by the admin container (uid 5005); the map must exist at startup.
[ -f /var/lib/dkim/selectors.map ] || : > /var/lib/dkim/selectors.map
chown 5005:5005 /var/lib/dkim /var/lib/dkim/selectors.map
chmod 750 /var/lib/dkim
chown -R 5005:5005 /var/lib/rspamd

rspamadm configtest

exec rspamd -f -u _rspamd -g _rspamd
