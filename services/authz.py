"""Authorization for Nyanjou actions.

Uses the existing ``allowed_users`` table managed by ``cogs/user_management.py``
(``/usr-mgmt add_user``). Users are identified by Discord user ID only. The
bot owner is always authorized.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("bot.py.nyanjou.authz")


class Authorizer:
    def __init__(
        self,
        db_path: Path | str,
        owner_id: int | None = None,
        owner_check: Callable[[int], Awaitable[bool]] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.owner_id = owner_id
        self._owner_check = owner_check

    def is_listed(self, user_id: int) -> bool:
        """True if the user ID is in allowed_users.sqlite. Fails closed."""
        if not self.db_path.exists():
            return False
        try:
            # Read-only URI so this module can never create or modify the DB.
            uri = f"file:{self.db_path}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=2.0) as db:
                row = db.execute(
                    "SELECT 1 FROM allowed_users WHERE user_id = ?", (int(user_id),)
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            log.exception("allowed_users lookup failed; denying")
            return False

    def is_owner_sync(self, user_id: int) -> bool:
        return self.owner_id is not None and int(user_id) == int(self.owner_id)

    async def is_owner(self, user_id: int) -> bool:
        if self.is_owner_sync(user_id):
            return True
        if self._owner_check is not None:
            try:
                return bool(await self._owner_check(int(user_id)))
            except Exception:
                log.exception("owner check failed; treating as non-owner")
        return False

    async def is_authorized(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        if await self.is_owner(user_id):
            return True
        return self.is_listed(user_id)

    def is_authorized_cached(self, user_id: int | None, known_owner_ids: set[int] | None = None) -> bool:
        """Synchronous variant for hot paths (audio thread); owner IDs must be pre-resolved."""
        if user_id is None:
            return False
        if self.is_owner_sync(user_id) or (known_owner_ids and user_id in known_owner_ids):
            return True
        return self.is_listed(user_id)
