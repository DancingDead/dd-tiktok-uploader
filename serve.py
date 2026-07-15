"""serve.py — lance l'usine en production via waitress (Flask + build React).

Un seul process sert l'API ET le front React (frontend/dist). À utiliser sur la
tour de déploiement (Windows) ; en dev on garde `webui.py` (serveur Flask) +
`npm run dev`.

    uv run python serve.py                 # écoute 0.0.0.0:8765

Variables d'environnement :
    HOST  (défaut 0.0.0.0 — joignable via Tailscale/LAN)
    PORT  (défaut 8765)

Prérequis : avoir construit le front une fois (`cd frontend && npm run build`).
"""

import os

from waitress import serve

from webui import create_app


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8765"))
    app = create_app()
    print(f"Usine en ligne (waitress) : http://{host}:{port}")
    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
