import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data"))

import auth
from auth import get_auth_headers


def test_raises_clearly_when_token_missing(monkeypatch):
    # Patch auth's own bound name (from `from dotenv import load_dotenv`),
    # not the dotenv module itself -- patching the module attribute
    # wouldn't affect auth.py's already-bound local reference. Needed
    # because the repo's real .env carries a real token; without this,
    # load_dotenv() would repopulate the var this test just deleted.
    monkeypatch.setattr(auth, "load_dotenv", lambda: None)
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="UPSTOX_ACCESS_TOKEN"):
        get_auth_headers()


def test_returns_bearer_header_shape(monkeypatch):
    monkeypatch.setattr(auth, "load_dotenv", lambda: None)
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "test-token-value")
    headers = get_auth_headers()
    assert headers["Authorization"] == "Bearer test-token-value"
    assert headers["Accept"] == "application/json"
