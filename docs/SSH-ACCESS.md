# Accès SSH distant à la tour Dancing Dead

Administrer l'usine (mises à jour du code, création de membres, redémarrage…) depuis le
Mac, via un terminal SSH. L'accès passe **uniquement par Tailscale** : le port 22 n'est
jamais exposé sur Internet (contrairement au site, publié en HTTPS via Tailscale Funnel).

## Installation (une seule fois, sur la tour)

Dans un **PowerShell administrateur** :

```powershell
cd "C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader"
powershell -ExecutionPolicy Bypass -File deploy\setup-ssh.ps1 -PublicKey "ssh-ed25519 AAAA... toi@mac"
```

`deploy/setup-ssh.ps1` est idempotent (relançable) et fait tout :

1. Installe **OpenSSH Server** (via Windows Update — peut prendre quelques minutes).
2. Service `sshd` en **démarrage automatique**, lancé.
3. **Shell par défaut = PowerShell** (au lieu de cmd).
4. Règle pare-feu **TCP 22 restreinte à `100.64.0.0/10`** (pairs Tailscale uniquement)
   et désactivation de la règle OpenSSH par défaut, trop large.
5. `LocalAccountTokenFilterPolicy=1` → les sessions SSH du compte admin local reçoivent
   un **jeton admin complet** (sinon UAC filtre le jeton et `restart-usine.ps1` ne peut
   pas tuer les process de la session 0).
6. Dépose la clé publique du Mac dans `C:\ProgramData\ssh\administrators_authorized_keys`
   avec l'ACL stricte imposée par sshd (SYSTEM + Administrateurs, sans héritage). Pour un
   membre du groupe Administrateurs, sshd lit **ce** fichier, pas `~/.ssh/authorized_keys`.

### Récupérer sa clé publique sur le Mac

```bash
# Générer une clé si besoin (Entrée x3) :
ssh-keygen -t ed25519 -C "mac-dd"
# Afficher la clé PUBLIQUE à coller dans -PublicKey :
cat ~/.ssh/id_ed25519.pub
```

## Utilisation quotidienne (depuis le Mac)

> ⚠️ **Se connecter à l'IP Tailscale `100.74.173.64`, PAS au nom `dancingdeadhq`.**
> Comme le **Funnel** est actif, le nom résout vers l'entrée publique HTTPS
> (`176.58.90.x`), qui ne relaie que le 443 → SSH y *timeout*. L'IP Tailscale pointe
> direct sur la tour où `sshd` écoute.

```bash
ssh "Dancing Dead@100.74.173.64"
```

Alias pratique — dans `~/.ssh/config` sur le Mac (bien mettre l'**IP**, pas le nom) :

```
Host tour
    HostName 100.74.173.64
    User Dancing Dead
    IdentityFile ~/.ssh/id_ed25519
```

Ensuite `ssh tour`, et on peut lancer des commandes sans session interactive :

```bash
# Mettre à jour : git pull -> uv sync -> build front -> redémarrage propre
ssh tour "cd 'C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader'; powershell -ExecutionPolicy Bypass -File deploy\update.ps1"

# Créer un membre
ssh tour "cd 'C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader'; uv run python db.py add-member alice"
```

`deploy/update.ps1` enchaîne `git pull` → `uv sync` → build du front → `restart-usine.ps1`
(qui tue proprement les serveurs `serve.py` de la session 0 avant de relancer la tâche).

## Sécurité

- SSH reste **dans le tunnel Tailscale** (WireGuard chiffré) ; port 22 fermé au reste du monde.
- L'auth par mot de passe reste **activée en secours**. Une fois la clé validée, on peut la
  couper : ajouter `PasswordAuthentication no` dans `C:\ProgramData\ssh\sshd_config`, puis
  `Restart-Service sshd`.

## Dépannage

- **`Add-WindowsCapability` semble figé** : il télécharge OpenSSH depuis Windows Update.
  Suivre la progression dans `C:\Windows\Logs\CBS\CBS.log` (`DownloadProgress [n / 100]`).
  Plan B si WU est bloqué : installer OpenSSH en portable depuis le zip GitHub
  `PowerShell/Win32-OpenSSH`.
- **`ssh tour` demande un mot de passe malgré la clé** : le compte est admin → vérifier que
  la clé est bien dans `administrators_authorized_keys` (pas `~/.ssh/`) et que l'ACL n'accorde
  qu'à SYSTEM + Administrateurs (sinon sshd ignore le fichier). `icacls` dans setup-ssh.ps1.
- **`ssh …@dancingdeadhq` *timeout*** : le nom résout vers l'entrée **publique du Funnel**
  (`176.58.90.x`, HTTPS uniquement), pas vers la tour → toujours passer par l'IP Tailscale
  `100.74.173.64` (voir l'avertissement plus haut).
