FROM nginx:1.27-alpine

# Only substitute our own variables - never nginx runtime variables like $host.
ENV NGINX_ENVSUBST_FILTER="^(MAIL_HOSTNAME|MESSAGE_SIZE_LIMIT_MB)$"

RUN rm -f /etc/nginx/conf.d/default.conf
COPY templates/ /etc/nginx/templates/
COPY 40-mailstack.sh /docker-entrypoint.d/40-mailstack.sh
RUN chmod +x /docker-entrypoint.d/40-mailstack.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
