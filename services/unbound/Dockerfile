FROM alpine:3.20

RUN apk add --no-cache unbound drill ca-certificates tzdata

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 53/udp 53/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD drill -Q @127.0.0.1 a.root-servers.net >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
