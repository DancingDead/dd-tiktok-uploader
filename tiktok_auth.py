"""tiktok_auth — connexion OAuth des comptes TikTok du label (sandbox puis prod).

Usage :
    uv run python tiktok_auth.py add      # connecter un compte (ouvre l'URL, colle le code)
    uv run python tiktok_auth.py list     # comptes connectés et validité des tokens
"""

import json
import secrets
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

REDIRECT_URI = "https://hooriiiii.github.io/dancingdead-site/tiktok-callback.html"
SCOPES = "user.info.basic,video.upload"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USERINFO_URL = "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name"
TOKENS_DIR = Path(__file__).parent / "tokens"
ENV_PATH = Path(__file__).parent / ".env"


def build_auth_url(client_key: str, redirect_uri: str, state: str) -> str:
    params = urllib.parse.urlencode(
        {
            "client_key": client_key,
            "response_type": "code",
            "scope": SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"https://www.tiktok.com/v2/auth/authorize/?{params}"


def parse_code_input(text: str) -> str:
    """Accepte le code brut OU l'URL de redirection complète collée telle quelle."""
    text = text.strip()
    if not text:
        raise ValueError("code vide")
    if "://" in text:
        query = urllib.parse.urlparse(text).query
        code = urllib.parse.parse_qs(query).get("code", [""])[0]
        if not code:
            raise ValueError("pas de paramètre ?code= dans l'URL collée")
        return code
    return text


def parse_env(text: str) -> dict:
    env: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("'\"")
    return env


# --- I/O ---------------------------------------------------------------------


def _load_credentials() -> tuple[str, str]:
    if not ENV_PATH.is_file():
        sys.exit(f"{ENV_PATH} introuvable (TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET)")
    env = parse_env(ENV_PATH.read_text())
    try:
        return env["TIKTOK_CLIENT_KEY"], env["TIKTOK_CLIENT_SECRET"]
    except KeyError as exc:
        sys.exit(f"variable manquante dans .env : {exc}")


def _post_form(url: str, fields: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def _get_json(url: str, access_token: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def exchange_and_store(code: str) -> dict:
    """Échange le code OAuth contre des tokens, vérifie via user/info, stocke.
    Retourne {open_id, display_name}. Lève RuntimeError en cas d'échec API."""
    client_key, client_secret = _load_credentials()
    token = _post_form(
        TOKEN_URL,
        {
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
    )
    if "access_token" not in token:
        raise RuntimeError(f"échec de l'échange du code : {json.dumps(token)}")

    info = _get_json(USERINFO_URL, token["access_token"]).get("data", {}).get("user", {})
    open_id = token.get("open_id", info.get("open_id", "inconnu"))
    display_name = info.get("display_name", "?")

    TOKENS_DIR.mkdir(exist_ok=True)
    token_path = TOKENS_DIR / f"{open_id}.json"
    token_path.write_text(
        json.dumps(
            {
                "open_id": open_id,
                "display_name": display_name,
                "access_token": token["access_token"],
                "refresh_token": token.get("refresh_token"),
                "expires_at": (datetime.now() + timedelta(seconds=token.get("expires_in", 0))).isoformat(),
                "scope": token.get("scope"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    token_path.chmod(0o600)
    return {"open_id": open_id, "display_name": display_name}


def add_account() -> None:
    client_key, _ = _load_credentials()
    state = secrets.token_urlsafe(12)
    print("1. Ouvre cette URL, connecte-toi avec le compte TikTok du label et autorise :\n")
    print(build_auth_url(client_key, REDIRECT_URI, state))
    print("\n2. La page de callback affiche un code — colle-le ici (ou colle l'URL entière).")
    code = parse_code_input(input("code > "))
    try:
        account = exchange_and_store(code)
    except RuntimeError as exc:
        sys.exit(str(exc))
    print(f"\nOK — compte connecté : {account['display_name']} (open_id {account['open_id']})")


def list_accounts() -> None:
    if not TOKENS_DIR.is_dir() or not any(TOKENS_DIR.glob("*.json")):
        print("aucun compte connecté (lance : uv run python tiktok_auth.py add)")
        return
    for path in sorted(TOKENS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        expires = data.get("expires_at", "?")
        print(f"  {data.get('display_name', '?'):24s} open_id {data.get('open_id')}  access expire {expires}")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "add"
    if command == "add":
        add_account()
    elif command == "list":
        list_accounts()
    else:
        sys.exit(f"commande inconnue : {command} (add | list)")


if __name__ == "__main__":
    main()
