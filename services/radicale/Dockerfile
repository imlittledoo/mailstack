FROM python:3.12-alpine

RUN pip install --no-cache-dir radicale==3.2.3 \
 && adduser -D -H -u 5006 radicale \
 && install -d -o 5006 -g 5006 /data

COPY config /etc/radicale/config

USER 5006
EXPOSE 5232

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:5232/.web/ || exit 1

CMD ["python", "-m", "radicale", "--config", "/etc/radicale/config"]
