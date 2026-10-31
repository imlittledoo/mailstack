FROM alpine:3.20

RUN apk add --no-cache certbot openssl curl tzdata

COPY acme.sh /acme.sh
RUN chmod +x /acme.sh

HEALTHCHECK --interval=5s --timeout=3s --retries=60 \
  CMD test -s /etc/ssl/mail/cert.pem && test -s /etc/ssl/mail/key.pem

ENTRYPOINT ["/acme.sh"]
