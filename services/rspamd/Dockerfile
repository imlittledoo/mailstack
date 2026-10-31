FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# Rspamd from the upstream repository (Debian's package lags far behind).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg tzdata \
 && install -d -m 0755 /etc/apt/keyrings \
 && curl -fsSL https://rspamd.com/apt-stable/gpg.key | gpg --dearmor -o /etc/apt/keyrings/rspamd.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/rspamd.gpg] https://rspamd.com/apt-stable/ bookworm main" > /etc/apt/sources.list.d/rspamd.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends rspamd \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# Fixed uid/gid 5005, shared with the admin container that writes DKIM keys.
RUN groupmod -g 5005 _rspamd \
 && usermod -u 5005 -g 5005 _rspamd \
 && install -d -o 5005 -g 5005 -m 0750 /var/lib/dkim \
 && install -d -o 5005 -g 5005 /var/lib/rspamd /run/rspamd \
 && chown -R 5005:5005 /var/lib/rspamd

COPY local.d/ /etc/rspamd/local.d/
COPY optional/ /etc/rspamd/optional/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11332 11334

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:11334/ping >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
