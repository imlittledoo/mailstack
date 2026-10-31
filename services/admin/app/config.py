import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Settings:
    mail_hostname = _env("MAIL_HOSTNAME", "mail.example.com").lower().rstrip(".")

    db_host = _env("DB_HOST", "mariadb")
    db_name = _env("DB_NAME", "mailstack")
    db_user = _env("DB_USER", "mailstack")
    db_password = _env("DB_PASSWORD")

    admin_user = _env("ADMIN_USER", "admin")
    admin_password = _env("ADMIN_PASSWORD")
    session_secret = _env("SESSION_SECRET")
    session_max_age = int(_env("SESSION_MAX_AGE", str(8 * 3600)))
    api_key = _env("API_KEY")

    rspamd_url = _env("RSPAMD_URL", "http://rspamd:11334")
    rspamd_password = _env("RSPAMD_PASSWORD")
    doveadm_url = _env("DOVEADM_URL", "http://dovecot:8080/doveadm/v1")
    doveadm_api_key = _env("DOVEADM_API_KEY")

    dkim_dir = _env("DKIM_DIR", "/var/lib/dkim")
    dkim_selector = _env("DKIM_SELECTOR", "dkim")
    dkim_key_bits = int(_env("DKIM_KEY_BITS", "2048"))

    mta_sts_mode = _env("MTA_STS_MODE", "testing")
    public_ipv4 = _env("PUBLIC_IPV4")
    public_ipv6 = _env("PUBLIC_IPV6")
    clamav_enabled = "clamav" in _env("COMPOSE_PROFILES").split(",")


settings = Settings()
