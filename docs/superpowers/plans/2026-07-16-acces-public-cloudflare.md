# Accès public Cloudflare — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Préparer l'app à l'exposition publique derrière Cloudflare Tunnel + Access : cookies de session durcis, ProxyFix, et runbook `docs/DEPLOYMENT.md` complété avec la procédure Cloudflare.

**Architecture:** Aucun changement fonctionnel — deux réglages dans `create_app` (`webui.py`) pilotés par l'environnement, et de la documentation. Les manipulations OVH/Cloudflare/tour restent manuelles le jour J, guidées par le runbook.

**Tech Stack:** Flask/werkzeug (déjà en dépendances — `ProxyFix` vient de werkzeug, rien à installer), pytest.

## Global Constraints

- Spec : `docs/superpowers/specs/2026-07-16-acces-public-cloudflare-design.md`
- `SESSION_COOKIE_HTTPONLY=True` et `SESSION_COOKIE_SAMESITE="Lax"` : **inconditionnels**
- `SESSION_COOKIE_SECURE` : activé **uniquement** si la variable d'env `PUBLIC_HTTPS=1` (le `.env` de la tour la posera ; absente en dev/local pour que `http://localhost:8765` continue de marcher)
- ProxyFix : `x_proto=1, x_host=1` (un seul proxy devant : cloudflared)
- Lancer les tests avec `uv run pytest` (le projet utilise uv, pas pip)
- Docs en français, ton et format alignés sur l'existant de `docs/DEPLOYMENT.md`

---

### Task 1: Durcissement des cookies de session + ProxyFix

**Files:**
- Modify: `webui.py:98-100` (bloc `app = Flask(__name__)` dans `create_app`)
- Test: `tests/test_webui_platform.py`

**Interfaces:**
- Consumes: `create_app(root=None)` existant (`webui.py:75`), fixture `client` existante (`tests/test_webui_platform.py:9`)
- Produces: `create_app` lit `os.environ["PUBLIC_HTTPS"]` ; config Flask `SESSION_COOKIE_*` posée ; `app.wsgi_app` enveloppé dans `ProxyFix`

- [ ] **Step 1: Write the failing tests**

Ajouter à la fin de `tests/test_webui_platform.py` :

```python
def test_session_cookie_hardening_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("PUBLIC_HTTPS", raising=False)
    app = create_app(root=tmp_path)
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    # sans PUBLIC_HTTPS, le cookie doit rester utilisable en http://localhost
    assert app.config["SESSION_COOKIE_SECURE"] is False


def test_session_cookie_secure_behind_public_https(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_HTTPS", "1")
    app = create_app(root=tmp_path)
    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_proxyfix_trusts_forwarded_proto(tmp_path):
    # derrière cloudflared, Flask doit voir le schéma/host publics
    from flask import request

    app = create_app(root=tmp_path)
    app.config["TESTING"] = True
    seen = {}

    # route hors /api : le before_request de login court-circuiterait
    # une route /api/* sans session (401 avant d'entrer dans la vue)
    @app.get("/_test_proto")
    def _test_proto():
        seen["scheme"] = request.scheme
        seen["host"] = request.host
        return ""

    client = app.test_client()
    client.get("/_test_proto", headers={
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "usine.example.com",
    })
    assert seen == {"scheme": "https", "host": "usine.example.com"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_webui_platform.py -k "cookie or proxyfix" -v`
Expected: 3 FAIL — `SESSION_COOKIE_HTTPONLY` vaut `True` par défaut chez Flask
donc ce assert passe, mais `SAMESITE` vaut `None` (≠ "Lax") et le test ProxyFix
voit `scheme == "http"`. Vérifier que chaque test échoue pour la bonne raison.

- [ ] **Step 3: Write minimal implementation**

Dans `create_app` (`webui.py`), juste après `app.config["PATHS"] = paths` :

```python
    # Exposition publique (Cloudflare Tunnel) : cookies durcis + proxy headers.
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("PUBLIC_HTTPS") == "1",
    )
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
```

`os` est déjà importé en tête de `webui.py` (vérifier ; sinon l'ajouter aux
imports du module).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_webui_platform.py -v`
Expected: tous PASS (les nouveaux + les existants — la fixture `client`
continue de fonctionner car sans `PUBLIC_HTTPS` le cookie n'est pas `Secure`).

Puis la suite entière : `uv run pytest` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webui.py tests/test_webui_platform.py
git commit -m "feat(webui): cookies durcis + ProxyFix pour l'exposition publique"
```

---

### Task 2: Runbook — section « Accès public (Cloudflare Tunnel + Access) »

**Files:**
- Modify: `docs/DEPLOYMENT.md` (remplacer la section 6 « Accès équipe », compléter l'intro, le `.env`, la checklist)

**Interfaces:**
- Consumes: variable `PUBLIC_HTTPS` créée en Task 1
- Produces: procédure jour J complète (migration DNS, tunnel, Access)

- [ ] **Step 1: Mettre à jour l'intro et le `.env`**

Dans `docs/DEPLOYMENT.md` :

1. Intro (lignes 3-5) : remplacer « **Tailscale** (accès équipe de partout) »
   par « **Cloudflare Tunnel + Access** (URL publique pour l'équipe ;
   Tailscale reste en accès admin de secours) ».
2. Section 3 (`.env`) : ajouter la ligne `PUBLIC_HTTPS=1` avec le commentaire
   `# cookies Secure derrière le tunnel HTTPS (ne pas poser en dev local)`.

- [ ] **Step 2: Remplacer la section 6 « Accès équipe »**

Nouveau contenu (adapter le placeholder `<domaine-dd>` est OK ici — c'est le
domaine réel, connu du lecteur, pas un TODO d'implémentation) :

```markdown
## 6. Accès public — Cloudflare Tunnel + Access

L'équipe accède à l'usine par une URL publique `https://usine.<domaine-dd>`,
sans rien installer. Deux portes : Cloudflare Access (code par email), puis le
login membre de l'app. Coût : 0 € (plan Free, ≤ 50 users).

### a) Migration DNS OVH → Cloudflare (une fois, ~30 min)

1. **Capture d'écran complète de la zone DNS OVH** (parade au risque n°1 :
   un enregistrement oublié, surtout les MX du mail).
2. Compte sur https://dash.cloudflare.com (Free) → « Add a site » → le domaine.
3. Cloudflare scanne la zone : vérifier ligne par ligne contre la capture
   (A/CNAME du site O2Switch, **MX**, TXT/SPF/DKIM).
4. Chez OVH (Web Cloud → Domaines → Serveurs DNS) : remplacer les DNS par les
   deux serveurs indiqués par Cloudflare.
5. Attendre la propagation (quelques heures). Site et mails inchangés.

### b) Tunnel (sur la tour, PowerShell)

```powershell
winget install --id=Cloudflare.cloudflared
cloudflared tunnel login                      # ouvre le navigateur, choisir le domaine
cloudflared tunnel create dd-usine
cloudflared tunnel route dns dd-usine usine.<domaine-dd>
```

Créer `C:\Users\<user>\.cloudflared\config.yml` :

```yaml
tunnel: dd-usine
credentials-file: C:\Users\<user>\.cloudflared\<TUNNEL_ID>.json
ingress:
  - hostname: usine.<domaine-dd>
    service: http://localhost:8765
  - service: http_status:404
```

Installer en service Windows (démarre au boot, avant le logon, se reconnecte
seul) :

```powershell
cloudflared service install
```

### c) Access (la porte email)

Dans https://one.dash.cloudflare.com → Access → Applications → « Add an
application » → Self-hosted :

- Application domain : `usine.<domaine-dd>`
- Policy « Équipe DD » : Action *Allow*, Include *Emails* → la liste des
  emails des membres
- Login method : **One-time PIN** (aucun compte à créer côté membres)
- Session duration : 30 jours

Départ d'un membre : retirer son email de la policy (accès coupé aussitôt),
en plus de son compte app.

### d) Côté membres

Ouvrir `https://usine.<domaine-dd>` → saisir son email → code reçu par mail →
puis login usine habituel. Le code n'est redemandé qu'au bout de 30 jours par
appareil.

> **Accès admin de secours** : Tailscale (voir historique git de ce fichier)
> reste utile pour dépanner la tour si le tunnel est en panne — optionnel.
```

- [ ] **Step 3: Mettre à jour la checklist post-reboot (section 8)**

Remplacer la ligne Tailscale par :

```markdown
- [ ] Le service `cloudflared` est démarré (`Get-Service cloudflared`).
- [ ] `https://usine.<domaine-dd>` s'ouvre depuis un appareil hors réseau du
      bureau : porte Access (code email) puis login usine OK.
```

et compléter la ligne « `http://localhost:8765` répond » — car avec
`PUBLIC_HTTPS=1` dans le `.env` de la tour, le **login** local en HTTP ne
posera pas le cookie (flag `Secure`). À documenter honnêtement :

```markdown
- [ ] `http://localhost:8765` répond (tâche DD-Usine active).
      NB : avec `PUBLIC_HTTPS=1`, le **login** local en http échouera
      silencieusement (cookie `Secure` non posé) — c'est attendu ; pour un
      vrai dépannage local connecté, retirer la variable du `.env` le temps
      de l'intervention, ou passer par l'URL publique.
```

- [ ] **Step 4: Relire le fichier en entier**

Vérifier la cohérence : plus aucune mention de Tailscale comme accès équipe
principal (intro, section 1 winget — y laisser Tailscale mais annoté
« optionnel, accès admin de secours » —, sections 5c, 6, 8).

- [ ] **Step 5: Commit**

```bash
git add docs/DEPLOYMENT.md
git commit -m "docs(deploy): accès public Cloudflare Tunnel + Access, Tailscale en secours"
```
