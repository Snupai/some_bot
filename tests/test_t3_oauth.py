"""OAuth for T3 MCP against a mock authorization server.

The mock mirrors the metadata of a real T3 server (2026-11-19 build): DCR,
PKCE S256, public client, authorization_code only.
"""
import base64
import hashlib
import json
import os
import stat
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from services import t3_oauth
from services.t3_oauth import (
    FileTokenStorage,
    T3AuthRequired,
    discover_grant_types,
    interactive_provider,
    non_interactive_provider,
)
from mcp.shared.auth import OAuthToken

BASE = "http://127.0.0.1:3773"
MCP = f"{BASE}/mcp"


class MockT3Auth:
    def __init__(self, grants=("authorization_code",)):
        self.grants = list(grants)
        self.challenge = None
        self.registered = []
        self.issued = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path
        if path == "/mcp":
            auth = request.headers.get("authorization", "")
            if auth in [f"Bearer {t}" for t in self.issued]:
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(401, headers={
                "www-authenticate": f'Bearer resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"'})
        if path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"resource": MCP, "authorization_servers": [BASE],
                                             "scopes_supported": ["orchestration:read", "orchestration:operate"],
                                             "bearer_methods_supported": ["header"]})
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={
                "issuer": BASE, "authorization_endpoint": f"{BASE}/oauth/mcp/authorize",
                "token_endpoint": f"{BASE}/oauth/mcp/token", "registration_endpoint": f"{BASE}/oauth/mcp/register",
                "response_types_supported": ["code"], "grant_types_supported": self.grants,
                "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": ["orchestration:read", "orchestration:operate"]})
        if path == "/oauth/mcp/register":
            body = json.loads(request.content)
            self.registered.append(body)
            return httpx.Response(201, json={**body, "client_id": "client-123", "client_id_issued_at": int(time.time())})
        if path == "/oauth/mcp/token":
            form = parse_qs(request.content.decode())
            verifier = form["code_verifier"][0]
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if form["code"][0] != "the-code" or digest != self.challenge:
                return httpx.Response(400, json={"error": "invalid_grant"})
            token = f"access-{len(self.issued) + 1}"
            self.issued.append(token)
            return httpx.Response(200, json={"access_token": token, "token_type": "bearer", "expires_in": 2592000,
                                             "scope": "orchestration:read orchestration:operate"})
        return httpx.Response(404)


async def test_interactive_login_stores_token_securely(tmp_path):
    mock = MockT3Auth()
    storage = FileTokenStorage(tmp_path / "data" / "t3_oauth.json", MCP)
    seen = {}

    async def redirect(url):
        q = parse_qs(urlparse(url).query)
        seen.update(q)
        mock.challenge = q["code_challenge"][0]

    async def callback():
        return "the-code", seen["state"][0]

    auth = interactive_provider(MCP, storage, ["authorization_code"], redirect, callback)
    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(mock)) as http:
        r = await http.post(MCP, json={})
    assert r.status_code == 200
    assert seen["code_challenge_method"] == ["S256"]
    assert "orchestration:operate" in seen.get("scope", [""])[0]
    assert mock.registered[0]["token_endpoint_auth_method"] == "none"
    assert mock.registered[0]["grant_types"] == ["authorization_code"]
    mode = stat.S_IMODE(os.stat(storage.path).st_mode)
    assert mode == 0o600
    assert storage.status()["signed_in"] is True
    tok = await storage.get_tokens()
    assert tok.access_token == "access-1"

    # The bot (non-interactive) reuses the stored token without a browser.
    bot_auth = non_interactive_provider(MCP, storage)
    async with httpx.AsyncClient(auth=bot_auth, transport=httpx.MockTransport(mock)) as http:
        r = await http.post(MCP, json={})
    assert r.status_code == 200


async def test_non_interactive_without_token_raises(tmp_path):
    mock = MockT3Auth()
    storage = FileTokenStorage(tmp_path / "t.json", MCP)
    auth = non_interactive_provider(MCP, storage)
    with pytest.raises(T3AuthRequired):
        async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(mock)) as http:
            await http.post(MCP, json={})


async def test_storage_expiry_and_url_binding(tmp_path):
    storage = FileTokenStorage(tmp_path / "t.json", MCP)
    await storage.set_tokens(OAuthToken(access_token="a", expires_in=100))
    assert (await storage.get_tokens()).access_token == "a"
    # Another MCP URL must not reuse the token.
    assert await FileTokenStorage(tmp_path / "t.json", "https://other/mcp").get_tokens() is None
    # Simulate expiry without refresh token.
    data = json.loads(storage.path.read_text())
    data["obtained_at"] = time.time() - 1000
    storage.path.write_text(json.dumps(data))
    assert await storage.get_tokens() is None
    # With a refresh token the (expired) token is still handed to the SDK so it can refresh.
    await storage.set_tokens(OAuthToken(access_token="b", expires_in=100, refresh_token="r"))
    data = json.loads(storage.path.read_text())
    data["obtained_at"] = time.time() - 1000
    storage.path.write_text(json.dumps(data))
    tok = await storage.get_tokens()
    assert tok.refresh_token == "r" and tok.expires_in == 0


@pytest.mark.parametrize("grants,expected", [
    (["authorization_code"], ["authorization_code"]),
    (["authorization_code", "refresh_token"], ["authorization_code", "refresh_token"]),
])
async def test_discover_grant_types(monkeypatch, grants, expected):
    mock = MockT3Auth(grants)
    real = httpx.AsyncClient
    monkeypatch.setattr(t3_oauth.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(mock), **kw))
    assert await discover_grant_types(MCP) == expected


async def test_existing_token_dir_permissions_untouched(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)
    storage = FileTokenStorage(shared / "t.json", MCP)
    await storage.set_tokens(OAuthToken(access_token="a"))
    assert stat.S_IMODE(os.stat(shared).st_mode) == 0o755
    assert stat.S_IMODE(os.stat(storage.path).st_mode) == 0o600
    fresh = FileTokenStorage(tmp_path / "new" / "t.json", MCP)
    await fresh.set_tokens(OAuthToken(access_token="b"))
    assert stat.S_IMODE(os.stat(tmp_path / "new").st_mode) == 0o700
