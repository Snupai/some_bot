"""OAuth for the T3 Code MCP server.

T3 Code's MCP endpoint uses standard MCP OAuth (verified against a T3 server
built 2026-11-19):

* ``/.well-known/oauth-protected-resource/mcp`` -> authorization server
* ``/.well-known/oauth-authorization-server``: dynamic client registration,
  PKCE ``S256``, public clients (``token_endpoint_auth_method=none``), scopes
  ``orchestration:read`` and ``orchestration:operate``
* ``grant_types_supported: ["authorization_code"]``: **no refresh tokens**.
  A sign-in lasts 30 days (T3 docs, "Removing an agent").

The MCP Python SDK's ``OAuthClientProvider`` implements discovery,
registration, PKCE and refresh. This module adds file-based token storage
(0600) and a non-interactive mode for the running bot: if the stored sign-in
expires, the bot reports that it needs a new login and does not hang waiting
for a browser.

One-time setup: ``uv run python tools/nyanjou_t3_login.py``.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

import httpx
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

log = logging.getLogger("bot.py.nyanjou.t3auth")

SCOPES = "orchestration:read orchestration:operate"
DEFAULT_CALLBACK_PORT = 33418


class T3AuthRequired(RuntimeError):
    """The stored T3 sign-in is missing or expired; run the login script."""


class FileTokenStorage(TokenStorage):
    """Stores OAuth client registration and tokens in a 0600 JSON file.

    Data is bound to the MCP server URL; switching ``T3_MCP_URL`` invalidates it.
    """

    def __init__(self, path: Path | str, server_url: str) -> None:
        self.path = Path(path)
        self.server_url = server_url

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            log.warning("T3 token file %s is unreadable; ignoring it", self.path)
            return {}
        if data.get("server_url") != self.server_url:
            return {}
        return data

    def _save(self, data: dict) -> None:
        data["server_url"] = self.server_url
        # mode only applies when the directory is created; an existing one keeps its permissions.
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".t3_oauth.", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    async def get_tokens(self) -> OAuthToken | None:
        data = self._load()
        raw = data.get("tokens")
        if not raw:
            return None
        token = OAuthToken.model_validate(raw)
        obtained = float(data.get("obtained_at") or 0)
        if token.expires_in and obtained:
            remaining = int(obtained + token.expires_in - time.time())
            if remaining <= 30 and not token.refresh_token:
                # Expired with no refresh token: report "no token" so the auth flow runs
                # (and fails fast in the bot with T3AuthRequired).
                return None
            token = token.model_copy(update={"expires_in": max(remaining, 0)})
        return token

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._load()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        data["obtained_at"] = time.time()
        self._save(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._load().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._load()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._save(data)

    def status(self) -> dict:
        """Non-secret summary for /status."""
        data = self._load()
        tokens = data.get("tokens") or {}
        out = {"signed_in": bool(tokens.get("access_token")), "refreshable": bool(tokens.get("refresh_token"))}
        if tokens.get("expires_in") and data.get("obtained_at"):
            out["expires_at"] = int(float(data["obtained_at"]) + int(tokens["expires_in"]))
        return out


async def discover_grant_types(server_url: str) -> list[str]:
    """Grant types to register: always authorization_code, plus refresh_token if offered."""
    grants = ["authorization_code"]
    parsed = urlparse(server_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            prm = await http.get(f"{base}/.well-known/oauth-protected-resource{path}")
            as_url = base
            if prm.status_code == 200:
                servers = prm.json().get("authorization_servers") or []
                if servers:
                    as_url = str(servers[0]).rstrip("/")
            asm = await http.get(f"{as_url}/.well-known/oauth-authorization-server")
            if asm.status_code == 200 and "refresh_token" in (asm.json().get("grant_types_supported") or []):
                grants.append("refresh_token")
    except (httpx.HTTPError, ValueError):
        log.debug("OAuth metadata discovery failed; registering authorization_code only", exc_info=True)
    return grants


def client_metadata(redirect_uri: str, grant_types: list[str]) -> OAuthClientMetadata:
    return OAuthClientMetadata(
        client_name="Nyanjou Discord Bot",
        redirect_uris=[redirect_uri],
        grant_types=grant_types,
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=SCOPES,
    )


def callback_uri(port: int = DEFAULT_CALLBACK_PORT) -> str:
    return f"http://localhost:{port}/callback"


def non_interactive_provider(server_url: str, storage: FileTokenStorage) -> OAuthClientProvider:
    """Provider for the running bot: refreshes if possible, never opens a browser."""

    async def redirect(_url: str) -> None:
        raise T3AuthRequired(
            "T3 Code sign-in missing or expired. Run: uv run python tools/nyanjou_t3_login.py"
        )

    async def callback() -> tuple[str, str | None]:
        raise T3AuthRequired("interactive sign-in is not available inside the bot")

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=client_metadata(callback_uri(), ["authorization_code", "refresh_token"]),
        storage=storage,
        redirect_handler=redirect,
        callback_handler=callback,
    )


def interactive_provider(
    server_url: str,
    storage: FileTokenStorage,
    grant_types: list[str],
    redirect_handler: Callable[[str], Awaitable[None]],
    callback_handler: Callable[[], Awaitable[tuple[str, str | None]]],
    port: int = DEFAULT_CALLBACK_PORT,
) -> OAuthClientProvider:
    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=client_metadata(callback_uri(port), grant_types),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        timeout=600,
    )
