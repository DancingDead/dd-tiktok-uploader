# Déploiement réel sur la tour — « as-built » (2026-07-17)

Ce document décrit **ce qui a été réellement installé et validé** sur la tour Windows
Dancing Dead, par opposition à [`DEPLOYMENT.md`](DEPLOYMENT.md) qui décrit le plan
générique. Il fait foi pour l'exploitation courante.

> Objectif atteint : l'usine **et** le LLM local tournent **au démarrage du PC, en
> arrière-plan invisible (session 0), sans qu'aucun utilisateur ne soit connecté**.
> La tour peut être utilisée normalement sur un autre compte Windows sans rien voir,
> tout remonte seul au boot et se répare en cas de crash.

---

## 1. Vue d'ensemble

| Brique | Où / comment | Accès |
|---|---|---|
| **Usine web** (Flask + build React via waitress) | Tâche `DD-Usine`, session 0 | port `8765` |
| **Accès équipe privé** | Tailscale (`dancingdeadhq` / `100.74.173.64`) | `http://dancingdeadhq:8765` |
| **Accès public HTTPS** | Tailscale **Funnel** | **https://dancingdeadhq.tail2611ce.ts.net/** |
| **Punchlines IA** | LM Studio **headless** (daemon llmster) sur GPU | API OpenAI locale `:1234` |
| **Sauvegarde** | Tâche `DD-Backup` quotidienne | `%USERPROFILE%\dd-backups` |

Emplacement du dépôt : `C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader`.

Matériel : NVIDIA RTX 4070 Ti (12 Go VRAM), 32 Go RAM, Windows 11.

---

## 2. Outils installés

| Outil | Version | Méthode |
|---|---|---|
| uv (Python) | 0.11.x | winget (`astral-sh.uv`) |
| ffmpeg | 8.1.2 | winget (`Gyan.FFmpeg`) |
| Node.js | 24 LTS | **zip portable** dans `%LOCALAPPDATA%\Programs\nodejs` |
| GitHub CLI (`gh`) | 2.96 | **zip portable** dans `%LOCALAPPDATA%\Programs\GitHub CLI` |
| Tailscale | 1.98 | winget (`Tailscale.Tailscale`) |
| LM Studio (app classique) | 0.4.19 | installeur officiel lmstudio.ai |

> ⚠️ Node et gh ont été posés en **portable** car les paquets MSI winget se bloquent
> en installation silencieuse (élévation UAC). uv/ffmpeg (paquets zip) et Tailscale
> (console admin interactive) passent bien par winget.
>
> ⚠️ **« Bionic »** (autre app de LM Studio = agent IA) n'est **pas** l'app dont on a
> besoin. L'app requise s'appelle littéralement « LM Studio ».

---

## 3. Build du front

```powershell
cd frontend
npm install
npm run build      # genere frontend/dist, servi par Flask (voir serve.py)
```

---

## 4. Comptes membres

Login obligatoire, aucun formulaire d'inscription. Créer chaque membre en CLI :

```powershell
uv run python db.py add-member <prenom>   # demande un mot de passe (masque)
uv run python db.py list-members
```

Premier compte créé : `theo`. ⚠️ Le login est **public** (via Funnel) sans limitation
de tentatives → **mots de passe solides obligatoires**.

---

## 5. Les 4 tâches planifiées

Toutes en **session 0**, **`LogonType S4U`** (compte `DancingDeadHQ\Dancing Dead`,
sans mot de passe stocké), **cachées**, déclenchées **au démarrage** — donc actives
sans qu'aucun utilisateur ne soit connecté. Créées en PowerShell **admin**.

| Tâche | Déclencheur | Rôle | Script |
|---|---|---|---|
| `DD-Usine` | Au démarrage | Sert l'usine web (port 8765), redémarrage auto ×3 | `deploy/start-usine.bat` |
| `DD-LMStudio` | Au démarrage | Démarre LM Studio headless + charge le modèle sur GPU | `deploy/start-lmstudio.bat` |
| `DD-LMStudio-Watchdog` | Toutes les 3 min | Relance LM Studio s'il est tombé | `deploy/watchdog-lmstudio.ps1` |
| `DD-Backup` | Quotidien 04:00 | Sauvegarde base + médias | `deploy/backup.ps1` |

Exemple de (ré)enregistrement — voir aussi `deploy/register-lmstudio-watchdog.ps1` :

```powershell
$user = "$env:USERDOMAIN\$env:USERNAME"
$action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader\deploy\start-usine.bat`""
$trigger   = New-ScheduledTaskTrigger -AtStartup
$settings  = New-ScheduledTaskSettingsSet -Hidden -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName "DD-Usine" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force
```

> L'auto-login Windows (netplwiz) n'est **pas** nécessaire : tout tourne en session 0.

---

## 6. Accès réseau (Tailscale + Funnel)

- Machine sur le tailnet : `dancingdeadhq` / `100.74.173.64` (MagicDNS activé).
- **Pare-feu** : port 8765 ouvert uniquement pour les pairs Tailscale :
  ```powershell
  New-NetFirewallRule -DisplayName "DD-Usine 8765 (Tailscale)" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -RemoteAddress 100.64.0.0/10 -Profile Any
  ```
- **Accès public HTTPS** via Funnel (persiste au reboot, cert automatique) :
  ```powershell
  & "C:\Program Files\Tailscale\tailscale.exe" funnel --bg 8765
  ```
  URL publique : **https://dancingdeadhq.tail2611ce.ts.net/**
- **Couper l'accès public** (revenir Tailscale-only) :
  ```powershell
  & "C:\Program Files\Tailscale\tailscale.exe" funnel --bg off
  ```

Onboarding d'un membre : installer Tailscale → rejoindre le même tailnet → ouvrir
l'URL publique (ou `http://100.74.173.64:8765`) → se connecter avec son compte.

---

## 7. LM Studio en headless (session 0, GPU)

Le modèle **`qwen2.5-7b-instruct-1m`** (Q4_K_M, ~4,7 Go) tourne via le **daemon
llmster** (cœur de LM Studio, sans GUI). **Le GPU fonctionne bien en session 0** sur
cette RTX 4070 Ti (validé : ~6 Go de VRAM utilisés, process en `SI=0`).

Séquence de démarrage (`deploy/start-lmstudio.bat`) :

```bat
"%LMS%" daemon up
"%LMS%" unload -a
"%LMS%" load qwen2.5-7b-instruct-1m --gpu max -y
"%LMS%" server start --port 1234
```
(`%LMS%` = `%USERPROFILE%\.lmstudio\bin\lms.exe`)

Configuration côté usine — fichier **`.env`** à la racine (non commité) :

```
LLM_BACKEND=lmstudio
LMSTUDIO_BASE_URL=http://localhost:1234/v1
LMSTUDIO_MODEL=qwen2.5-7b-instruct-1m
```

> ⚠️ **Garder `.env` en ASCII pur** (pas d'accents ni d'emoji) : `beatsync._load_dotenv`
> le lit en encodage Windows (cp1252) et planterait sinon.
>
> Repli optionnel vers l'API Claude si LM Studio est coupé :
> `LLM_FALLBACK=anthropic` + `ANTHROPIC_API_KEY=...`.

### Watchdog (redémarrage en cas de crash)

`deploy/watchdog-lmstudio.ps1` (toutes les 3 min) : si le serveur `:1234` ne répond
pas ou que le modèle n'est plus chargé, il relance **proprement**. La récupération est
**idempotente** : `unload -a` **avant** `load`, sinon `lms load` empile des instances
en double (`qwen...:2`) qui saturent la VRAM. Validé : crash → relance seule en < 3 min,
toujours une seule instance.

---

## 8. Correctif de code

**`beatsync.py` → `_call_lmstudio`** : le format de sortie structurée est passé de
`response_format: {"type": "json_object"}` à `json_schema`. LM Studio ≥ 0.4 rejette
`json_object` avec une erreur HTTP 400. Les 159 tests passent toujours.

---

## 9. Opérations courantes

**Vérifier l'état complet :**
```powershell
Get-ScheduledTask -TaskName "DD-*" | Select TaskName, State
Invoke-WebRequest http://localhost:8765/ -UseBasicParsing        # usine
Invoke-RestMethod http://localhost:1234/v1/models                # LM Studio
& "C:\Users\Dancing Dead\.lmstudio\bin\lms.exe" ps               # modele charge
```

**Ajouter un membre :** `uv run python db.py add-member <prenom>` (effet immédiat).

**Mettre à jour le code :**
```powershell
git pull
uv sync
cd frontend; npm install; npm run build; cd ..
Stop-ScheduledTask DD-Usine;  Start-ScheduledTask DD-Usine
```
> En cas de conflit sur `deploy/*.bat` ou `beatsync.py`, garder la version locale (adaptée à la tour).

**Relancer LM Studio à la main :** `powershell -File deploy\watchdog-lmstudio.ps1`
(ou `Start-ScheduledTask DD-LMStudio`).

---

## 10. Checklist après reboot (validée)

- [x] `DD-Usine` remonte l'usine en session 0 (site local + public en HTTP 200).
- [x] Tailscale (service) + Funnel remontent → URL publique joignable.
- [x] `DD-LMStudio` démarre le daemon + charge le modèle sur GPU (session 0).
- [x] `DD-LMStudio-Watchdog` répare en < 3 min en cas de crash.
- [x] Génération de punchlines de bout en bout OK.
