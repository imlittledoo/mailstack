"""MariaDB access and schema migrations.

The admin service owns the schema. Postfix and Dovecot only read it, using the
queries in their entrypoints - keep those in sync when changing tables.
"""

import contextlib
import datetime
import logging
import time
from typing import Any, Iterator

import pymysql
import pymysql.cursors

from .config import settings

log = logging.getLogger("mailstack.db")


def connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=settings.db_host,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=5,
    )


@contextlib.contextmanager
def transaction() -> Iterator[pymysql.cursors.DictCursor]:
    """Yield a cursor; commit on success, roll back on any exception."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, args: Any = None) -> list[dict]:
    with transaction() as cur:
        cur.execute(sql, args)
        return [clean(r) for r in cur.fetchall()]


def query_one(sql: str, args: Any = None) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def clean(row: dict) -> dict:
    """Make a DB row JSON friendly."""
    out = {}
    for key, value in row.items():
        if isinstance(value, (datetime.datetime, datetime.date)):
            value = value.isoformat()
        elif key == "active" or key.startswith("is_"):
            value = bool(value)
        out[key] = value
    return out


def wait_for_database(timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            connect().close()
            return
        except pymysql.MySQLError as exc:
            if time.monotonic() > deadline:
                raise
            log.info("waiting for database: %s", exc)
            time.sleep(2)


TABLE_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, [
        f"""CREATE TABLE IF NOT EXISTS domains (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(253) NOT NULL UNIQUE,
            description VARCHAR(255) NOT NULL DEFAULT '',
            max_mailboxes INT NOT NULL DEFAULT 0,
            max_aliases INT NOT NULL DEFAULT 0,
            default_quota_mb INT NOT NULL DEFAULT 1024,
            max_quota_mb INT NOT NULL DEFAULT 0,
            total_quota_mb INT NOT NULL DEFAULT 0,
            active TINYINT(1) NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS mailboxes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            domain_id INT NOT NULL,
            local_part VARCHAR(64) NOT NULL,
            username VARCHAR(320) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            name VARCHAR(255) NOT NULL DEFAULT '',
            quota_mb INT NOT NULL DEFAULT 1024,
            active TINYINT(1) NOT NULL DEFAULT 1,
            last_login DATETIME NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_domain_local (domain_id, local_part),
            CONSTRAINT fk_mailbox_domain FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS aliases (
            id INT AUTO_INCREMENT PRIMARY KEY,
            domain_id INT NOT NULL,
            address VARCHAR(320) NOT NULL UNIQUE,
            goto TEXT NOT NULL,
            description VARCHAR(255) NOT NULL DEFAULT '',
            active TINYINT(1) NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_alias_domain FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS alias_domains (
            id INT AUTO_INCREMENT PRIMARY KEY,
            alias_domain VARCHAR(253) NOT NULL UNIQUE,
            target_domain_id INT NOT NULL,
            active TINYINT(1) NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_aliasdomain_target FOREIGN KEY (target_domain_id) REFERENCES domains(id) ON DELETE CASCADE
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS admins (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(64) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            active TINYINT(1) NOT NULL DEFAULT 1,
            last_login DATETIME NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS dkim_keys (
            domain VARCHAR(253) NOT NULL PRIMARY KEY,
            selector VARCHAR(63) NOT NULL,
            bits INT NOT NULL,
            private_key TEXT NOT NULL,
            public_key TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS audit_log (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            actor VARCHAR(320) NOT NULL,
            action VARCHAR(64) NOT NULL,
            target VARCHAR(320) NOT NULL DEFAULT '',
            details VARCHAR(1000) NOT NULL DEFAULT '',
            ip VARCHAR(45) NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_created (created_at)
        ) {TABLE_OPTS}""",
    ]),
    # Two-factor auth, domain administrators, app passwords.
    (2, [
        *(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}"
            for table in ("admins", "mailboxes")
            for column in (
                "totp_secret VARCHAR(64) NULL",
                "totp_last_step BIGINT NOT NULL DEFAULT 0",
                "recovery_codes TEXT NULL",
            )
        ),
        "ALTER TABLE admins ADD COLUMN IF NOT EXISTS role VARCHAR(16) NOT NULL DEFAULT 'superadmin' AFTER password_hash",
        f"""CREATE TABLE IF NOT EXISTS admin_domains (
            admin_id INT NOT NULL,
            domain_id INT NOT NULL,
            PRIMARY KEY (admin_id, domain_id),
            CONSTRAINT fk_admindomain_admin FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE CASCADE,
            CONSTRAINT fk_admindomain_domain FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS app_passwords (
            id INT AUTO_INCREMENT PRIMARY KEY,
            mailbox_id INT NOT NULL,
            name VARCHAR(100) NOT NULL,
            secret_sha256 CHAR(64) NOT NULL UNIQUE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_apppw_mailbox FOREIGN KEY (mailbox_id) REFERENCES mailboxes(id) ON DELETE CASCADE
        ) {TABLE_OPTS}""",
    ]),
]


def migrate() -> None:
    with transaction() as cur:
        cur.execute(f"CREATE TABLE IF NOT EXISTS schema_version (version INT NOT NULL) {TABLE_OPTS}")
        cur.execute("SELECT MAX(version) AS v FROM schema_version")
        current = cur.fetchone()["v"] or 0
    for version, statements in MIGRATIONS:
        if version <= current:
            continue
        log.info("applying schema migration %d", version)
        # DDL auto-commits in MariaDB, so every statement must be idempotent (IF NOT EXISTS, ...).
        with transaction() as cur:
            for stmt in statements:
                cur.execute(stmt)
            cur.execute("INSERT INTO schema_version (version) VALUES (%s)", (version,))
