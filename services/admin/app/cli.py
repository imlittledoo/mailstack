"""Maintenance commands, run inside the admin container:

    python -m app.cli reset-admin <username> [password]
"""

import secrets
import sys

from . import db, validation
from .security import hash_password


def reset_admin(username: str, password: str | None = None) -> None:
    username = validation.admin_username(username)
    password = password or secrets.token_urlsafe(15)
    validation.password(password)
    with db.transaction() as cur:
        cur.execute("SELECT id FROM admins WHERE username=%s", (username,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE admins SET password_hash=%s, active=1 WHERE id=%s", (hash_password(password), row["id"]))
        else:
            cur.execute("INSERT INTO admins (username, password_hash) VALUES (%s, %s)", (username, hash_password(password)))
        cur.execute(
            "INSERT INTO audit_log (actor, action, target, details) VALUES ('cli', 'admin.reset', %s, '')",
            (username,),
        )
    print(f"Admin '{username}' password set to: {password}")


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "reset-admin":
        try:
            reset_admin(argv[1], argv[2] if len(argv) > 2 else None)
        except Exception as exc:  # noqa: BLE001
            print(f"error: {getattr(exc, 'detail', exc)}", file=sys.stderr)
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
