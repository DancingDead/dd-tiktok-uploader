# Tuto — piloter la tour depuis le Mac en SSH

But : mettre à jour le code, créer des membres, redémarrer l'usine, éditer des
fichiers… **sans se déplacer devant la tour**. Tout passe par Tailscale (chiffré,
port 22 jamais exposé à Internet).

> ⚠️ **Connecte-toi à l'IP Tailscale `100.74.173.64`, pas au nom `dancingdeadhq`.**
> Le nom résout vers l'entrée **publique du Funnel** (HTTPS uniquement) → SSH y
> *timeout*. L'IP Tailscale pointe direct sur la tour où `sshd` écoute.

---

## 1. Réglage une fois (sur le Mac)

Ajoute cet alias à `~/.ssh/config` :

```
Host tour
    HostName 100.74.173.64
    User Dancing Dead
    IdentityFile ~/.ssh/id_ed25519
```

Ensuite tout se fait avec `ssh tour`. La clé du Mac est déjà autorisée sur la tour
(pas de mot de passe demandé). La session SSH a un **jeton administrateur complet**
→ les commandes qui élèvent (redémarrage de l'usine, registre…) marchent directement.

Vérifie que la tour est joignable :

```bash
tailscale status | grep -i dancingdead   # doit être "online" (pas "offline")
ssh "Dancing Dead@100.74.173.64"                       # doit répondre : DancingDeadHQ
```

Si `offline` : la tour n'est pas sur le tailnet (éteinte, réseau coupé, ou reboot en
cours) → rien à faire en SSH tant qu'elle n'est pas revenue.

---

## 2. Le bon flux pour « éditer la tour » : Mac → git → déploiement

On **n'édite pas le code à la main sur la tour**. On édite sur le Mac, on pousse sur
GitHub, puis on déclenche le déploiement à distance.

```bash
# 1. Sur le Mac : éditer, committer, pousser
git add -A
git commit -m "..."
git push

# 2. Déployer sur la tour (git pull → uv sync → build front → redémarrage propre)
ssh tour "cd 'C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader'; powershell -ExecutionPolicy Bypass -File deploy\update.ps1"
```

`deploy/update.ps1` fait tout l'enchaînement et relance l'usine sans couper la
session 0. Après ça, l'app tourne avec le nouveau code.

---

## 3. Commandes utiles au quotidien

Le chemin du repo sur la tour est long ; on le pose dans une variable pour lisibilité :

```bash
REPO="cd 'C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader';"

# Créer un membre d'équipe (demande un mot de passe, en interactif)
ssh -t tour "$REPO uv run python db.py add-member alice"

# Lister les membres
ssh tour "$REPO uv run python db.py list-members"

# Redémarrer l'usine (sans redéployer)
ssh tour "$REPO powershell -ExecutionPolicy Bypass -File deploy\restart-usine.ps1"

# Vérifier que l'app répond en local sur la tour
ssh tour "(Invoke-WebRequest http://localhost:8765 -UseBasicParsing).StatusCode"

# État des 4 tâches planifiées (usine, LM Studio, watchdog, backup)
ssh tour "Get-ScheduledTask DD-* | Select TaskName, State"

# Tailscale : accès public (Funnel) on/off
ssh tour "& 'C:\Program Files\Tailscale\tailscale.exe' funnel status"
```

> `ssh -t` force un pseudo-terminal : utile quand la commande **attend une saisie**
> (ex. le mot de passe de `add-member`). Sinon, `ssh tour "..."` suffit.

---

## 4. Éditer un fichier ponctuel directement sur la tour

Pour un petit changement hors git (un `.env`, un test rapide) :

- **Le plus confortable : VS Code Remote-SSH.** Dans VS Code (Mac) → extension
  *Remote - SSH* → « Connect to Host » → `tour` → ouvre le dossier du repo. Tu édites
  comme en local, les fichiers vivent sur la tour.
- **En ligne de commande** (PowerShell, pas de `nano` par défaut sous Windows) :
  ```bash
  # Lire un fichier
  ssh tour "$REPO Get-Content .env"
  # Écrire/remplacer un fichier (attention, écrase)
  ssh tour "$REPO Set-Content .env 'LLM_BACKEND=lmstudio'"
  ```
  `notepad` ne marche pas en SSH (interface graphique, session 0).

---

## 5. Dépannage express

| Symptôme | Cause probable | Solution |
|---|---|---|
| `ssh tour` timeout | tu utilises le nom `dancingdeadhq` (Funnel) | passe par l'IP `100.74.173.64` (déjà le cas dans l'alias) |
| `offline` dans `tailscale status` | tour hors tailnet (éteinte/reboot/réseau) | attendre son retour, ou aller la rallumer |
| SSH demande un mot de passe | clé non lue (compte admin) | la clé doit être dans `administrators_authorized_keys` sur la tour (voir `deploy/setup-ssh.ps1`) |
| `update.ps1` échoue | build front ou process bloqué | relancer ; en dernier recours `restart-usine.ps1` |

---

Réf. complète de l'install SSH : [`docs/SSH-ACCESS.md`](SSH-ACCESS.md).
Réf. déploiement réel de la tour : [`docs/DEPLOYMENT-TOWER.md`](DEPLOYMENT-TOWER.md).
