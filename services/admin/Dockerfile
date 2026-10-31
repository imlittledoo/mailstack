FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# uid/gid 5005 is shared with rspamd, which reads the DKIM keys written here.
RUN groupadd -g 5005 mailstack \
 && useradd -u 5005 -g 5005 -d /app -M -s /usr/sbin/nologin mailstack \
 && install -d -o 5005 -g 5005 -m 0750 /var/lib/dkim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app/ app/

USER 5005
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=12 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
