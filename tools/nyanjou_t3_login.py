#!/usr/bin/env python3
"""One-time T3 Code sign-in for the Nyanjou bot (MCP OAuth with PKCE).

    uv run python tools/nyanjou_t3_login.py            # opens a browser, catches the redirect on localhost
    uv run python tools/nyanjou_t3_login.py --paste    # headless: open the URL anywhere, paste the final URL back

What happens:
1. The script discovers T3's OAuth settings from T3_MCP_URL and registers the
   bot as a public OAuth client (dynamic client registration).
2. You open the sign-in page and approve the bot in T3 Code. Pick an access
   level that can start threads ("Supervised" or higher); "Read only" can't
   run tasks.
3. The token is written to T3_MCP_TOKEN_FILE (default data/t3_oauth.json,
   mode 0600, gitignored).
4. The script connects once, lists the T3 tools and your projects, and
   prints the project IDs for T3_PROJECT_ID.

T3 sign-ins last 30 days and T3 does not issue refresh tokens (Nov 2026), so
rerun this script when /status says "Anmeldung nötig".
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import httpx  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from services.config import NyanjouConfig, check_mcp_url  # noqa: E402
from services.t3_mcp_client import parse_tool_result  # noqa: E402
from services.t3_oauth import (  # noqa: E402
    DEFAULT_CALLBACK_PORT,
    FileTokenStorage,
    discover_grant_types,
    interactive_provider,
)


def parse_redirect(url: str) -> tuple[str, str | None]:
    qs = parse_qs(urlparse(url.strip()).query)
    if "error" in qs:
        raise SystemExit(f"Sign-in failed: {qs.get('error')} {qs.get('error_description', [''])[0]}")
    code = qs.get("code", [None])[0]
    if not code:
        raise SystemExit("No ?code= in that URL.")
    return code, qs.get("state", [None])[0]


class _CallbackServer:
    def __init__(self, port: int) -> None:
        self.result: tuple[str, str | None] | None = None
        self.error: str | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    outer.result = parse_redirect(self.path)
                    body = "Nyanjou ist jetzt mit T3 Code verbunden. Du kannst dieses Fenster schließen."
                    self.send_response(200)
                except SystemExit as exc:
                    outer.error = str(exc)
                    body = f"Fehler: {exc}"
                    self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", port), Handler)
        Thread(target=self.httpd.serve_forever, daemon=True).start()

    async def wait(self, timeout: float = 600) -> tuple[str, str | None]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self.result is None and self.error is None:
            if loop.time() > deadline:
                raise SystemExit("Timed out waiting for the sign-in redirect.")
            await asyncio.sleep(0.2)
        self.httpd.shutdown()
        if self.error:
            raise SystemExit(self.error)
        return self.result  # type: ignore[return-value]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paste", "--no-browser", action="store_true", dest="paste",
                    help="don't start a local callback server; paste the final redirect URL instead")
    ap.add_argument("--port", type=int, default=DEFAULT_CALLBACK_PORT)
    ap.add_argument("--url", help="override T3_MCP_URL")
    ap.add_argument("--status", action="store_true", help="only show the stored sign-in status")
    args = ap.parse_args()

    cfg = NyanjouConfig.from_env()
    url = args.url or cfg.t3_mcp_url
    if not url:
        print("T3_MCP_URL is not set (.env). Copy it from T3 Code: Settings → Environments → Copy MCP URL.")
        return 2
    check_mcp_url(url)
    storage = FileTokenStorage(cfg.t3_token_file, url)
    if args.status:
        print(json.dumps(storage.status(), indent=2))
        return 0

    grants = await discover_grant_types(url)
    print(f"T3 MCP: {url}\nRegistering grant types: {grants}")
    server = None if args.paste else _CallbackServer(args.port)

    async def redirect_handler(auth_url: str) -> None:
        print("\nOpen this URL, sign in to T3 Code and approve 'Nyanjou Discord Bot':\n")
        print(f"  {auth_url}\n")
        print("Choose an access level that can start threads (Supervised or higher).")
        if not args.paste:
            webbrowser.open(auth_url)

    async def callback_handler() -> tuple[str, str | None]:
        if server is not None:
            print(f"Waiting for the redirect on http://localhost:{args.port}/callback ...")
            return await server.wait()
        loop = asyncio.get_running_loop()
        pasted = await loop.run_in_executor(
            None, input, "After approving, paste the full URL from the browser's address bar (it may show an error page): "
        )
        return parse_redirect(pasted)

    auth = interactive_provider(url, storage, grants, redirect_handler, callback_handler, port=args.port)
    async with httpx.AsyncClient(auth=auth, timeout=httpx.Timeout(30, read=120)) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = sorted(t.name for t in (await session.list_tools()).tools)
                print(f"\n✅ Signed in. Token saved to {cfg.t3_token_file} (mode 0600).")
                print(f"T3 tools available ({len(tools)}): {', '.join(tools)}")
                if "t3_project_list" in tools:
                    res = parse_tool_result(await session.call_tool("t3_project_list", {"limit": 50}))
                    print("\nProjects (set T3_PROJECT_ID to one of these IDs):")
                    for p in res.get("projects", []):
                        print(f"  {p.get('id')}  {p.get('title')}  ({p.get('workspaceRoot')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
