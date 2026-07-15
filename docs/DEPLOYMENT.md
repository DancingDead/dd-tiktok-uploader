# Déploiement — tour Windows (auto-hébergée)

L'usine tourne sur une **tour Windows au bureau, allumée 24/7**, qui héberge tout :
Flask (via waitress) + le build React, la base SQLite, les médias, le rendu vidéo,
**LM Studio** (LLM local, coût nul) et **Tailscale** (accès équipe de partout).

> ⚠️ Ces scripts et commandes sont écrits pour Windows et **n'ont pas été exécutés
> sur la tour** — à valider au premier déploiement. Le code applicatif, lui, est testé.

---

## 1. Prérequis à installer sur la tour (une fois)

Dans un terminal **PowerShell** :

```powershell
winget install --id=astral-sh.uv        # uv (Python)
winget install --id=OpenJS.NodeJS.LTS   # Node.js (build du front)
winget install --id=Gyan.FFmpeg         # ffmpeg (rendu vidéo) — vérifier: ffmpeg -version
winget install --id=tailscale.tailscale # accès équipe
```

- **LM Studio** : télécharger depuis https://lmstudio.ai puis installer.
- Redémarrer le terminal pour que le PATH soit à jour (`uv --version`, `node --version`, `ffmpeg -version`).

## 2. Récupérer le projet et construire

```powershell
git clone <URL_DU_REPO> C:\dd\dd-tiktok-uploader
cd C:\dd\dd-tiktok-uploader
uv sync                       # dépendances Python
cd frontend
npm install
npm run build                 # génère frontend/dist (servi par Flask)
cd ..
```

Créer un membre d'équipe (à répéter par personne) :

```powershell
uv run python db.py add-member <prenom>   # demande un mot de passe
```

## 3. Configurer le LLM local (`.env`)

Créer un fichier `.env` à la racine :

```
LLM_BACKEND=lmstudio
LMSTUDIO_BASE_URL=http://localhost:1234/v1
LMSTUDIO_MODEL=<nom-du-modele-charge>
# Repli optionnel vers Claude si LM Studio est coupé (sinon punchlines vides) :
# LLM_FALLBACK=anthropic
# ANTHROPIC_API_KEY=sk-ant-...
```

Dans **LM Studio** : télécharger un modèle open source (ex. un Qwen/Llama/Mistral
instruct quantisé qui tient dans la VRAM du GPU), le **charger**, puis démarrer le
serveur local (onglet « Developer » → *Start server*, ou en CLI `lms server start`).
Le port par défaut est `1234`. Renseigner `LMSTUDIO_MODEL` avec l'identifiant du
modèle chargé (visible dans LM Studio).

> Le rendu ne bloque jamais sur le LLM : si LM Studio est indisponible, les punchlines
> tombent à vide et la vidéo est produite sans sous-titres.

## 4. Vérifier manuellement avant automatisation

```powershell
# Terminal 1 — LM Studio server démarré (étape 3)
# Terminal 2 :
cd C:\dd\dd-tiktok-uploader
uv run python serve.py        # -> http://0.0.0.0:8765
```

Ouvrir http://localhost:8765, se connecter, créer une niche, générer une variante.
Si tout marche, passer à l'automatisation.

## 5. Démarrage automatique au boot

La tour étant dédiée et allumée en permanence, le plus simple est **connexion
automatique + lancement au logon** (LM Studio a besoin d'une session ouverte pour
le GPU).

**a) Connexion automatique Windows** d'un utilisateur dédié : `netplwiz` → décocher
« Les utilisateurs doivent entrer un nom… » → saisir le compte + mot de passe.
(Alternative : `Autologon` de Sysinternals.)

**b) Tâches planifiées « à l'ouverture de session »** (PowerShell **admin**) :

```powershell
$repo = "C:\dd\dd-tiktok-uploader"

# L'usine (Flask + React via waitress), avec redémarrage auto en cas de plantage
schtasks /Create /TN "DD-Usine" /SC ONLOGON /RL LIMITED ^
  /TR "cmd /c %repo%\deploy\start-usine.bat"

# LM Studio + serveur local (adapter le chemin d'install si besoin)
schtasks /Create /TN "DD-LMStudio" /SC ONLOGON ^
  /TR "cmd /c \"%LOCALAPPDATA%\Programs\lm-studio\LM Studio.exe\" & lms server start"
```

> Pour le redémarrage auto en cas de plantage, ouvrir « Planificateur de tâches » →
> tâche **DD-Usine** → onglet *Paramètres* → « En cas d'échec, redémarrer toutes les
> 1 minute, jusqu'à 3 fois ».

**c) Tailscale** : au premier lancement, `tailscale up` (connexion au compte). Cocher
« Run at startup » dans l'app. Récupérer l'IP/nom Tailscale de la tour.

## 6. Accès équipe

Chaque membre installe **Tailscale**, rejoint le même réseau (ton compte / tailnet),
puis ouvre `http://<nom-tailscale-de-la-tour>:8765` et se connecte avec ses
identifiants (créés à l'étape 2). Aucun port ouvert sur la box.

## 7. Sauvegarde

Planifier `deploy\backup.ps1` (base + médias vers un dossier daté) :

```powershell
schtasks /Create /TN "DD-Backup" /SC DAILY /ST 04:00 ^
  /TR "powershell -ExecutionPolicy Bypass -File C:\dd\dd-tiktok-uploader\deploy\backup.ps1"
```

Idéalement, faire pointer la sauvegarde vers **un autre disque** ou un NAS :
`... backup.ps1 -Dest D:\dd-backups`.

## 8. Checklist de démarrage (après un reboot)

- [ ] La tour a ouvert la session automatiquement.
- [ ] LM Studio est lancé, un modèle est **chargé**, le serveur écoute sur `1234`.
- [ ] `http://localhost:8765` répond (tâche DD-Usine active).
- [ ] Tailscale est connecté (`tailscale status`).
- [ ] Depuis un autre appareil sur le tailnet : `http://<tour>:8765` s'ouvre et le login marche.
- [ ] Une génération de test produit une vidéo **avec** punchlines (LLM local OK).

## Mise à jour du code

```powershell
cd C:\dd\dd-tiktok-uploader
git pull
uv sync
cd frontend && npm install && npm run build && cd ..
# puis relancer la tâche DD-Usine (ou rebooter)
schtasks /End /TN "DD-Usine"  &  schtasks /Run /TN "DD-Usine"
```
