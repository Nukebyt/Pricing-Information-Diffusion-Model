"""Auth for Upstox's public+read-only market-data endpoints.

This project's own copy, kept deliberately small: get_ws_authorized_url()
is the only thing this project's tick_recorder.py actually calls, and it
only ever needed the Analytics-token path (BUGS.md DEC-2) -- not a full
OAuth2 authorization-code flow, which this project has no use for.

Uses the Analytics access token (static, 365-day validity) rather than the
full OAuth2 authorization-code flow. No redirect flow, no refresh logic:
just attach the token.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv


def get_auth_headers() -> dict:
    load_dotenv()
    token = os.environ.get("UPSTOX_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "UPSTOX_ACCESS_TOKEN not set in .env -- generate an Analytics access token at "
            "account.upstox.com/developer/apps (Analytics tab) and drop it in .env. "
            "See ROADMAP.md S0/S7."
        )
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def get_ws_authorized_url() -> str:
    """Upstox's WS auth is a two-step authorize-then-connect pattern: a
    REST call with the Bearer token returns a short-lived, pre-authorized
    wss:// URL with a single-use `code` embedded in its query string.
    Connect directly to that URL -- no separate auth headers needed on the
    WS handshake itself, the code in the URL IS the auth.

    Two things the docs' prose got wrong, caught by testing live rather
    than trusting the page (2026-08-25): the endpoint is under /v3/, not
    /v2/ (the /v2/ path returns 410 Gone -- retired, not just
    undocumented), and the response field is `authorizedRedirectUri`
    (camelCase), not the `authorized_redirect_uri` (snake_case) the docs
    page showed.
    """
    import requests

    resp = requests.get(
        "https://api.upstox.com/v3/feed/market-data-feed/authorize",
        headers=get_auth_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["data"]["authorizedRedirectUri"]
