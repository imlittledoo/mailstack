import re

from fastapi import HTTPException

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
# RFC 5322 dot-atom, minus "+" which is reserved as the recipient delimiter.
_LOCAL = re.compile(r"^[a-z0-9!#$%&'*/=?^_`{|}~-]+(\.[a-z0-9!#$%&'*/=?^_`{|}~-]+)*$")
_EXTERNAL_LOCAL = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_SELECTOR = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ADMIN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


def invalid(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


def domain(value: str) -> str:
    v = value.strip().lower().rstrip(".")
    try:
        v = v.encode("idna").decode("ascii")
    except UnicodeError:
        raise invalid(f"Invalid domain name: {value!r}")
    labels = v.split(".")
    if (
        len(v) > 253
        or len(labels) < 2
        or not all(_LABEL.match(label) for label in labels)
        or labels[-1].isdigit()
    ):
        raise invalid(f"Invalid domain name: {value!r}")
    return v


def local_part(value: str) -> str:
    v = value.strip().lower()
    if not v or len(v) > 64 or not _LOCAL.match(v):
        raise invalid(f"Invalid local part: {value!r} (letters, digits and . _ - are safe; '+' is reserved)")
    return v


def email(value: str, *, external: bool = False) -> str:
    """Normalize an address. External addresses may contain '+' and are not ours to restrict."""
    v = value.strip().lower()
    local, sep, dom = v.rpartition("@")
    if not sep or not local:
        raise invalid(f"Invalid email address: {value!r}")
    if external:
        if len(local) > 64 or not _EXTERNAL_LOCAL.match(local):
            raise invalid(f"Invalid email address: {value!r}")
    else:
        local = local_part(local)
    return f"{local}@{domain(dom)}"


def password(value: str) -> str:
    if len(value) < 8:
        raise invalid("Password must be at least 8 characters")
    if len(value.encode()) > 72:
        raise invalid("Password must be at most 72 bytes")
    return value


def selector(value: str) -> str:
    v = value.strip().lower()
    if not _SELECTOR.match(v):
        raise invalid("Invalid DKIM selector (letters, digits and '-')")
    return v


def admin_username(value: str) -> str:
    v = value.strip().lower()
    if not _ADMIN.match(v) or "@" in v:
        raise invalid("Admin username must be 2-64 characters: letters, digits, . _ -")
    return v
