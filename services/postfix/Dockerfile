FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN echo "postfix postfix/main_mailer_type select No configuration" | debconf-set-selections \
 && apt-get update \
 && apt-get install -y --no-install-recommends postfix postfix-mysql ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY conf/main.cf conf/master.cf /etc/postfix/
COPY entrypoint.sh /entrypoint.sh
COPY cert-watch.sh /usr/local/bin/cert-watch
RUN chmod +x /entrypoint.sh /usr/local/bin/cert-watch

EXPOSE 25 465 587

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD postfix status >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/entrypoint.sh"]
