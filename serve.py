"""Point d'entrée production : sert la webui via Waitress (WSGI).

À utiliser sur la tour Windows, derrière Tailscale Funnel. Waitress écoute en
local (127.0.0.1) ; seul tailscaled, sur la même machine, s'y connecte et
expose le dashboard en HTTPS sur une URL *.ts.net (cf. deploy/RUNBOOK.md).

    uv run python serve.py

Variables d'environnement reconnues :
    DD_HOST     interface d'écoute      (défaut 127.0.0.1 — ne pas exposer au LAN)
    DD_PORT     port d'écoute           (défaut 8765)
    DD_THREADS  threads Waitress        (défaut 8)
    DD_BEHIND_HTTPS_PROXY  forcé à "1" ici (ProxyFix + cookie Secure, cf. webui)
"""

import os

from waitress import serve

from webui import create_app


def main() -> None:
    # On est toujours derrière le proxy HTTPS (Tailscale Funnel) en production.
    os.environ.setdefault("DD_BEHIND_HTTPS_PROXY", "1")
    host = os.environ.get("DD_HOST", "127.0.0.1")
    port = int(os.environ.get("DD_PORT", "8765"))
    threads = int(os.environ.get("DD_THREADS", "8"))
    app = create_app()
    print(f"webui (Waitress) → http://{host}:{port}  threads={threads}", flush=True)
    serve(app, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
