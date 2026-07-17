# Runbook — déploiement de l'usine à vidéos sur la tour Windows

Cible : **tour Windows 10/11 dans les bureaux Dancing Dead**. Le dashboard sort
en ligne via **Tailscale Funnel** (URL `https://<machine>.<tailnet>.ts.net`),
accessible par toute l'équipe **sans rien installer**. Accès admin **SSH via
Tailscale**. Tout démarre seul au boot, sans session ouverte.

> **Aucun changement DNS.** Le domaine `dancingdeadrecords.com` (OVH), le site
> (o2switch), les emails `@dancingdeadrecords.com` (OVH) et la boutique Shopify
> **ne sont pas touchés**. On n'utilise ni Cloudflare, ni les nameservers.

On repart de zéro : la tour devient la **seule** source des données (base, sons,
clips, vidéos produites) → penser sauvegardes (§10).

Architecture :

```
  Équipe (n'importe où)                 Toi (admin, n'importe où)
        │                                       │
  https://<machine>.<tailnet>.ts.net      ssh dd@<ip-tailscale>
        │  ↓ Tailscale Funnel (HTTPS)           │  ↓ chiffré Tailscale
        ▼                                        ▼
  ┌──────────────────── Tour Windows (bureaux DD) ─────────────────────┐
  │  tailscaled (Funnel HTTPS + réseau privé + SSH)                     │
  │        └► Waitress → webui Flask 127.0.0.1:8765                     │
  │  user "dd" : repo + uv + ffmpeg + données (platform.db,tracks,clips)│
  └────────────────────────────────────────────────────────────────────┘
```

> La webui reste sur **127.0.0.1** : jamais exposée directement au réseau. C'est
> `tailscaled` (Funnel) qui termine le HTTPS et relaie vers elle en local.
> Rien à ouvrir sur la box des bureaux.
>
> ⚠️ **Funnel = URL publique sur Internet.** La seule barrière est le **login
> membre** de l'appli (mots de passe hachés). C'est acceptable pour un dashboard
> d'équipe ; si tu veux plus fermé, voir la variante « tailnet privé » en §6.

---

## 0. Prérequis

- [ ] Accès admin à la tour.
- [ ] Un compte **Tailscale** (gratuit) — celui du label.
- [ ] Ta clé SSH publique du Mac (`~/.ssh/id_ed25519.pub` ; sinon
      `ssh-keygen -t ed25519` sur le Mac).
- [ ] L'URL du repo git : `https://github.com/DancingDead/dd-tiktok-uploader.git`.

*(Aucune démarche DNS / domaine / Cloudflare : rien à préparer à l'avance.)*

---

## 1. Compte Windows `dd`

- [ ] Paramètres → Comptes → Autres utilisateurs → Ajouter un compte.
- [ ] « Je ne dispose pas des informations de connexion » → « Ajouter un
      utilisateur sans compte Microsoft » → nom **`dd`**, mot de passe fort
      (le noter dans le gestionnaire de mots de passe du label).
- [ ] Le passer **Administrateur** le temps de l'installation.
- [ ] **Se connecter à la session `dd`** et faire toute la suite depuis elle.

---

## 2. Dépendances système (PowerShell dans la session `dd`)

Ouvrir **PowerShell en administrateur**. Le script `deploy/install.ps1`
automatise les §2 à §8 ; on peut aussi tout faire à la main :

```powershell
winget install --id Git.Git --scope machine
winget install --id astral-sh.uv
winget install --id Gyan.FFmpeg --scope machine     # ffmpeg + ffprobe pour tous
winget install --id tailscale.tailscale
# NSSM : via winget si dispo, sinon https://nssm.cc/download (dézipper, mettre
# nssm.exe dans un dossier du PATH, ex. C:\Tools\nssm\)
winget install --id NSSM.NSSM
```

- [ ] **Rouvrir un terminal** puis vérifier le PATH :
      `git --version`, `uv --version`, `ffmpeg -version`, `ffprobe -version`,
      `tailscale version`, `nssm` (affiche l'aide).

> ⚠️ FFmpeg **doit** être installé en `--scope machine` : un service Windows ne
> voit pas le PATH par-utilisateur. Si `ffprobe` n'est pas trouvé par le
> service plus tard, c'est presque toujours ça.

---

## 3. Récupérer le projet (on repart de zéro)

```powershell
cd C:\Users\dd
git clone https://github.com/DancingDead/dd-tiktok-uploader.git dd-tiktok-uploader
cd dd-tiktok-uploader
git checkout feat/deploy-prod    # contient serve.py + deploy/ (tant que non mergé)
uv sync
uv run pytest        # tout vert = environnement bon
```

- [ ] Tests verts.
- [ ] Créer les membres de l'équipe (un par personne) :
      `uv run python db.py add-member <nom>` (il demandera un mot de passe).
- [ ] Lancer une fois la webui en local pour vérifier :
      `uv run python webui.py` → ouvrir http://127.0.0.1:8765, se connecter,
      puis Ctrl+C. (Uploader sons/clips se fera ensuite via l'UI en ligne.)

---

## 4. Serveur de production (Waitress)

Le rendu public passe par **Waitress** (pas le serveur Flask de dev), via
`serve.py`. Test manuel avant de le passer en service :

```powershell
$env:DD_BEHIND_HTTPS_PROXY = "1"
uv run python serve.py           # doit afficher "webui (Waitress) → http://127.0.0.1:8765"
```

- [ ] Ctrl+C pour arrêter. On l'installera en service au §7.

---

## 5. Tailscale (réseau privé : SSH admin + Funnel)

```powershell
tailscale up
```

- [ ] Suivre le lien d'authentification, connecter la tour au tailnet du label.
- [ ] Noter l'IP `100.x.x.x` (ou le nom MagicDNS) de la tour : `tailscale ip -4`.
- [ ] Sur ton **Mac** : installer Tailscale et `tailscale up` (même compte) →
      les deux machines se voient.
- [ ] **Activer HTTPS pour le tailnet** (requis par Funnel) : admin console
      Tailscale → **DNS** → activer **MagicDNS** puis **HTTPS Certificates**.

---

## 6. Publier le dashboard via Funnel

Funnel expose la webui locale sur une URL publique HTTPS `*.ts.net`.

```powershell
tailscale funnel --bg 8765       # proxie https://<machine>.<tailnet>.ts.net → localhost:8765
tailscale funnel status          # affiche l'URL publique à partager à l'équipe
```

- [ ] Au **premier** `tailscale funnel`, Tailscale peut afficher un lien pour
      **autoriser Funnel** sur ce nœud dans l'admin console → cliquer, accepter,
      relancer la commande.
- [ ] Avec la webui qui tourne (§4 ou service §7), ouvrir l'URL `*.ts.net`
      depuis un autre appareil → le login du dashboard s'affiche. ✅
- [ ] `--bg` rend le Funnel **persistant** : il est ré-appliqué automatiquement
      au boot par le service Tailscale (pas de service en plus à créer).
- [ ] Partager l'URL `*.ts.net` + leur compte membre à l'équipe.

> **Variante « tailnet privé »** (plus fermé, si tu ne veux PAS d'URL publique) :
> ne pas lancer Funnel. À la place, chaque membre installe Tailscale et rejoint
> le tailnet ; ils accèdent à `http://<machine>.<tailnet>.ts.net:8765` (ou via
> `tailscale serve` en HTTPS interne). Plus sûr, mais install requise pour tous.

---

## 7. Tout démarrer au boot (services Windows, sans session)

Un service Windows démarre **au boot, sans session ouverte**, et se relance
seul. `deploy/install.ps1 -Stage services` fait le webui ; sinon à la main :

### 7a. Tailscale — service installé par défaut à l'install. Vérifier :
```powershell
Get-Service Tailscale        # Status Running, StartType Automatic
```
Le Funnel configuré en §6 (`--bg`) est ré-appliqué par ce service au boot.

### 7b. webui / Waitress — via NSSM (chemins ABSOLUS, sinon échec au boot) :
```powershell
$repo = "C:\Users\dd\dd-tiktok-uploader"
$uv   = (Get-Command uv).Source          # chemin absolu de uv.exe
$ff   = Split-Path (Get-Command ffmpeg).Source   # dossier de ffmpeg/ffprobe

nssm install dd-webui "$uv" "run python serve.py"
nssm set dd-webui AppDirectory "$repo"
nssm set dd-webui AppEnvironmentExtra "DD_BEHIND_HTTPS_PROXY=1" "PATH=$ff;%PATH%"
nssm set dd-webui Start SERVICE_AUTO_START
nssm set dd-webui AppStdout "$repo\data\webui.log"
nssm set dd-webui AppStderr "$repo\data\webui.log"
nssm start dd-webui
```

- [ ] **Compte du service** : par défaut il tourne en LocalSystem. Pour qu'il
      tourne sous `dd` (fichiers appartenant à `dd`) : `nssm edit dd-webui` →
      onglet **Log on** → *This account* → `.\dd` + mot de passe. Sinon
      LocalSystem convient pour une tour dédiée.
- [ ] Vérifier : `Get-Service dd-webui` = Running, et l'URL `*.ts.net` répond.

---

## 8. Accès SSH admin (OpenSSH via Tailscale)

```powershell
# Serveur SSH natif Windows
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service sshd -StartupType Automatic
Start-Service sshd
```

- [ ] Autoriser ta **clé publique** : créer
      `C:\Users\dd\.ssh\authorized_keys` et y coller le contenu de ton
      `id_ed25519.pub`. (Pour un compte admin, Windows lit aussi
      `C:\ProgramData\ssh\administrators_authorized_keys` — y mettre la clé si
      `dd` est admin.)
- [ ] Depuis le **Mac** : `ssh dd@<ip-tailscale>` doit passer **sans mot de
      passe**. (SSH ne transite que par le réseau Tailscale, pas par la box.)

---

## 9. Validation « ça démarre vraiment tout seul »

La seule preuve qui compte :

- [ ] **Redémarrer la tour.** Ne PAS ouvrir de session.
- [ ] Depuis un autre appareil : l'URL `*.ts.net` répond (Funnel + login OK).
- [ ] Depuis le Mac : `ssh dd@<ip-tailscale>` passe.
- [ ] `Get-Service Tailscale, dd-webui, sshd` → tous *Running*.
- [ ] `tailscale funnel status` → montre toujours le mapping vers `localhost:8765`.

---

## 10. Sauvegardes (la tour est la seule copie)

Données critiques, toutes sous `C:\Users\dd\dd-tiktok-uploader\` :
`platform.db`, `tracks\`, `clips\`, `data\` (dont les vidéos produites).

Script fourni : **`deploy/backup.ps1`** (snapshot à chaud de la base +
copie non destructive des médias vers un disque externe).

- [ ] Brancher un disque externe (ex. lettre `E:`).
- [ ] Test manuel :
      `powershell -ExecutionPolicy Bypass -File deploy\backup.ps1 -Dest E:\dd-backup`
      → vérifier `E:\dd-backup\db-snapshots\` (snapshot daté) et
      `E:\dd-backup\current\` (tracks/clips/data).
- [ ] Automatiser (tâche quotidienne à 03:00, tourne sans session ouverte) :
      `powershell -ExecutionPolicy Bypass -File deploy\backup.ps1 -Dest E:\dd-backup -Register`
- [ ] Vérifier la tâche : `Get-ScheduledTask dd-backup`. La lancer une fois
      à la main : `Start-ScheduledTask dd-backup`, puis relire `E:\dd-backup\backup.log`.

> La sauvegarde ne **supprime jamais** côté disque externe (robocopy `/E /XO`)
> : un fichier effacé par erreur sur la tour reste récupérable. Les snapshots
> de base sont datés et purgés après `-KeepDays` jours (défaut 30). Si le
> disque est débranché, la tâche sort proprement sans erreur.
> Destination réseau/cloud (lecteur mappé) : éditer la tâche pour la faire
> tourner sous le compte `dd` (SYSTEM ne voit pas les lecteurs mappés).

---

## 11. Mémo — update / fix à distance

Depuis le Mac, une fois tout en place :

```bash
ssh dd@<ip-tailscale>
cd dd-tiktok-uploader
git pull
uv sync                     # si dépendances changées
nssm restart dd-webui       # relance la webui
Get-Content data\webui.log -Tail 40   # lire les logs si besoin
```

Tailscale (et donc le Funnel) se reconnecte seul : un simple `git pull` +
`nssm restart dd-webui` suffit pour la plupart des mises à jour.
