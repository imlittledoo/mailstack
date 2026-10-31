#!/bin/sh
# Validating, recursive resolver for the stack. A private resolver is required
# for DNS blocklists (Spamhaus & co. refuse queries from public resolvers).
set -eu

NET="${IPV4_NETWORK:-172.22.1}"
CONF=/etc/unbound/unbound.conf

cat > "$CONF" <<EOF
server:
  verbosity: 1
  interface: 0.0.0.0
  port: 53
  do-ip4: yes
  do-ip6: no
  do-udp: yes
  do-tcp: yes
  access-control: 0.0.0.0/0 refuse
  access-control: 127.0.0.0/8 allow
  access-control: ${NET}.0/24 allow
  hide-identity: yes
  hide-version: yes
  harden-glue: yes
  harden-dnssec-stripped: yes
  harden-below-nxdomain: yes
  qname-minimisation: yes
  prefetch: yes
  prefetch-key: yes
  num-threads: 2
  so-reuseport: yes
  msg-cache-size: 32m
  rrset-cache-size: 64m
  username: "unbound"
  directory: "/etc/unbound"
  chroot: ""
  use-syslog: no
  logfile: ""
  pidfile: ""
  auto-trust-anchor-file: "/var/lib/unbound/root.key"
EOF

if [ -n "${DNS_FORWARDERS:-}" ]; then
  echo "forward-zone:" >> "$CONF"
  echo "  name: \".\"" >> "$CONF"
  for fwd in $DNS_FORWARDERS; do
    echo "  forward-addr: $fwd" >> "$CONF"
  done
fi

mkdir -p /var/lib/unbound
# unbound-anchor exits 1 when it had to (re)write the anchor - that is not an error.
unbound-anchor -a /var/lib/unbound/root.key || true
chown -R unbound:unbound /var/lib/unbound

unbound-checkconf "$CONF"
exec unbound -d -c "$CONF"
