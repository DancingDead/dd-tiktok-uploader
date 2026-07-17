"""Tests de la config de déploiement production de webui.

Derrière le tunnel Cloudflare (HTTPS terminé par Cloudflare, trafic http en
local vers cloudflared → Waitress), il faut :
- faire confiance à X-Forwarded-Proto (ProxyFix) pour que Flask sache qu'on
  est en https (cookies sécurisés, redirections correctes) ;
- marquer le cookie de session Secure pour qu'il ne parte jamais en clair.
En dev local (http), ces deux réglages casseraient le login, donc ils sont
gardés derrière la variable d'environnement DD_BEHIND_HTTPS_PROXY.
"""

from werkzeug.middleware.proxy_fix import ProxyFix

from webui import create_app


def test_cookie_hardening_always_on(tmp_path, monkeypatch):
    monkeypatch.delenv("DD_BEHIND_HTTPS_PROXY", raising=False)
    app = create_app(root=tmp_path)
    # durcissement inoffensif appliqué partout
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    # mais PAS Secure en dev local (sinon le cookie ne part jamais en http)
    assert app.config.get("SESSION_COOKIE_SECURE") is not True
    assert not isinstance(app.wsgi_app, ProxyFix)


def test_https_proxy_mode_hardens_and_wraps(tmp_path, monkeypatch):
    monkeypatch.setenv("DD_BEHIND_HTTPS_PROXY", "1")
    app = create_app(root=tmp_path)
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert isinstance(app.wsgi_app, ProxyFix)