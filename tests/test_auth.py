"""Tests des parties pures de tiktok_auth : URL d'autorisation, parsing du code, .env."""

import pytest

from tiktok_auth import build_auth_url, parse_code_input, parse_env

REDIRECT = "https://hooriiiii.github.io/dancingdead-site/tiktok-callback.html"


def test_build_auth_url_contains_required_params():
    url = build_auth_url("sbawKEY", REDIRECT, state="abc123")
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/?")
    assert "client_key=sbawKEY" in url
    assert "response_type=code" in url
    assert "scope=user.info.basic%2Cvideo.upload" in url
    assert "state=abc123" in url
    assert "redirect_uri=https%3A%2F%2Fhooriiiii.github.io" in url


def test_parse_code_input_accepts_raw_code():
    assert parse_code_input("abcDEF123") == "abcDEF123"


def test_parse_code_input_accepts_full_redirect_url():
    url = f"{REDIRECT}?code=abcDEF123&scopes=user.info.basic&state=xyz"
    assert parse_code_input(url) == "abcDEF123"


def test_parse_code_input_strips_whitespace():
    assert parse_code_input("  abcDEF123\n") == "abcDEF123"


def test_parse_code_input_rejects_empty():
    with pytest.raises(ValueError):
        parse_code_input("   ")


def test_parse_env_reads_keys_ignores_comments():
    text = "# commentaire\nTIKTOK_CLIENT_KEY=sbaw123\n\nTIKTOK_CLIENT_SECRET='sec ret'\nAUTRE=x\n"
    env = parse_env(text)
    assert env["TIKTOK_CLIENT_KEY"] == "sbaw123"
    assert env["TIKTOK_CLIENT_SECRET"] == "sec ret"
