from fastapi import APIRouter

from . import account, admins, alias_domains, aliases, auth, dkim_keys, domains, mailboxes, me, system

router = APIRouter(prefix="/api/v1")
for module in (auth, account, system, domains, mailboxes, aliases, alias_domains, dkim_keys, admins, me):
    router.include_router(module.router)
