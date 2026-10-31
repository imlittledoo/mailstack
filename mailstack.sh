#!/usr/bin/env bash
# MailStack management helper. Run ./mailstack.sh help for usage.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
PROJECT=mailstack
DC=(docker compose)

die() { echo "error: $*" >&2; exit 1; }
gen() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-32}" || true; }
need_env() { [ -f .env ] || die ".env not found - run ./mailstack.sh init first"; }
env_get() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2-; }

cmd_init() {
  if [ -f .env ] && [ "${FORCE:-0}" != 1 ]; then
    die ".env already exists (set FORCE=1 to overwrite - existing secrets will be lost!)"
  fi
  local host="${1:-}" email="${2:-}"
  if [ -z "$host" ]; then
    read -rp "Mail server hostname (FQDN, e.g. mail.example.com): " host
  fi
  host="$(echo "$host" | tr '[:upper:]' '[:lower:]')"
  [[ "$host" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$ ]] || die "'$host' is not a valid FQDN"
  if [ -z "$email" ] && [ -t 0 ]; then
    read -rp "Email for Let's Encrypt expiry notices (optional): " email
  fi
  local tz="UTC"
  if [ -f /etc/timezone ]; then tz="$(cat /etc/timezone)";
  elif command -v timedatectl >/dev/null 2>&1; then tz="$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)"; fi

  local admin_pw; admin_pw="$(gen 20)"
  sed \
    -e "s|^MAIL_HOSTNAME=.*|MAIL_HOSTNAME=$host|" \
    -e "s|^TZ=.*|TZ=$tz|" \
    -e "s|^DB_PASSWORD=.*|DB_PASSWORD=$(gen 32)|" \
    -e "s|^DB_ROOT_PASSWORD=.*|DB_ROOT_PASSWORD=$(gen 32)|" \
    -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$admin_pw|" \
    -e "s|^SESSION_SECRET=.*|SESSION_SECRET=$(gen 48)|" \
    -e "s|^API_KEY=.*|API_KEY=$(gen 40)|" \
    -e "s|^RSPAMD_PASSWORD=.*|RSPAMD_PASSWORD=$(gen 24)|" \
    -e "s|^DOVEADM_API_KEY=.*|DOVEADM_API_KEY=$(gen 40)|" \
    -e "s|^ACME_EMAIL=.*|ACME_EMAIL=$email|" \
    .env.example > .env
  chmod 600 .env
  cat <<EOF
Created .env for $host

  Admin panel:  https://$host/
  Username:     admin
  Password:     $admin_pw
  Rspamd UI:    https://$host/rspamd/  (password: RSPAMD_PASSWORD in .env)

Before starting, point an A record for $host at this server and open ports
25 465 587 993 995 143 110 4190 80 443. Then run: ./mailstack.sh up
EOF
}

cmd_backup() {
  need_env
  local dest="${1:-./backups}/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$dest"
  dest="$(cd "$dest" && pwd)"
  echo "Backing up to $dest"
  "${DC[@]}" exec -T mariadb sh -c 'exec mariadb-dump --single-transaction --routines -uroot -p"$MARIADB_ROOT_PASSWORD" --all-databases' \
    | gzip > "$dest/mariadb.sql.gz"
  for vol in vmail dkim rspamd redis certs letsencrypt roundcube-db radicale; do
    if docker volume inspect "${PROJECT}_$vol" >/dev/null 2>&1; then
      echo "  volume $vol"
      docker run --rm -v "${PROJECT}_$vol:/data:ro" -v "$dest:/backup" alpine:3.20 \
        tar czf "/backup/$vol.tar.gz" -C /data .
    fi
  done
  cp .env "$dest/env"
  chmod 600 "$dest/env"
  echo "Backup complete: $dest"
}

cmd_restore() {
  local src="${1:-}"
  [ -d "$src" ] || die "usage: ./mailstack.sh restore <backup directory>"
  src="$(cd "$src" && pwd)"
  read -rp "This OVERWRITES all current mail data with $src. Type 'yes' to continue: " ok
  [ "$ok" = yes ] || die "aborted"
  [ -f .env ] || cp "$src/env" .env
  "${DC[@]}" down
  for archive in "$src"/*.tar.gz; do
    local vol; vol="$(basename "$archive" .tar.gz)"
    echo "  volume $vol"
    docker volume create "${PROJECT}_$vol" >/dev/null
    docker run --rm -v "${PROJECT}_$vol:/data" -v "$src:/backup:ro" alpine:3.20 \
      sh -c "find /data -mindepth 1 -delete && tar xzf /backup/$vol.tar.gz -C /data"
  done
  "${DC[@]}" up -d mariadb
  echo "Waiting for MariaDB..."
  until "${DC[@]}" exec -T mariadb healthcheck.sh --connect --innodb_initialized >/dev/null 2>&1; do sleep 2; done
  gunzip -c "$src/mariadb.sql.gz" | "${DC[@]}" exec -T mariadb sh -c 'exec mariadb -uroot -p"$MARIADB_ROOT_PASSWORD"'
  "${DC[@]}" up -d
  echo "Restore complete."
}

cmd_help() {
  cat <<'EOF'
Usage: ./mailstack.sh <command> [args]

Setup
  init [hostname] [email]   Generate .env with random secrets
  up                        Build images and start the stack
  down                      Stop the stack
  restart [service]         Restart everything or one service
  update                    Rebuild images with fresh base images and restart

Operations
  ps                        Show service status
  logs [service]            Follow logs
  queue                     Show the Postfix mail queue
  flush                     Retry delivery of all queued mail
  reset-admin <user> [pw]   Reset (or create) an admin account
  purge-orphans [--delete]  List/delete maildirs of deleted mailboxes
  doveadm <args>            Run doveadm in the Dovecot container
  backup [dir]              Back up database, mail and keys (default ./backups)
  restore <dir>             Restore a backup created by 'backup'
EOF
}

command="${1:-help}"
shift || true
case "$command" in
  init) cmd_init "$@" ;;
  up) need_env; "${DC[@]}" up -d --build "$@"; echo "Admin panel: https://$(env_get MAIL_HOSTNAME)/" ;;
  down) "${DC[@]}" down "$@" ;;
  restart) need_env; "${DC[@]}" restart "$@" ;;
  update) need_env; "${DC[@]}" build --pull; "${DC[@]}" pull --ignore-buildable; "${DC[@]}" up -d ;;
  ps|status) "${DC[@]}" ps ;;
  logs) "${DC[@]}" logs -f --tail=200 "$@" ;;
  queue) "${DC[@]}" exec postfix postqueue -p ;;
  flush) "${DC[@]}" exec postfix postqueue -f && echo "Queue flushed" ;;
  reset-admin) [ $# -ge 1 ] || die "usage: reset-admin <username> [password]"; "${DC[@]}" exec admin python -m app.cli reset-admin "$@" ;;
  purge-orphans) "${DC[@]}" exec dovecot purge-orphans "$@" ;;
  doveadm) "${DC[@]}" exec dovecot doveadm "$@" ;;
  backup) cmd_backup "$@" ;;
  restore) cmd_restore "$@" ;;
  help|-h|--help) cmd_help ;;
  *) cmd_help; exit 1 ;;
esac
