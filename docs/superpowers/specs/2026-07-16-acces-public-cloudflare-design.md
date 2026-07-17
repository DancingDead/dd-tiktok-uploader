# Accès public à l'usine — Cloudflare Tunnel + Access

Date : 2026-07-16
Statut : validé (brainstorming avec Théo)

## Objectif

Rendre le dashboard de l'usine accessible par une **URL publique**
(`usine.<domaine-dd>`) depuis n'importe quel navigateur, sans que l'équipe
installe quoi que ce soit — tout en gardant l'hébergement sur la tour Windows
du bureau (le PC de Kévin), la génération locale (FFmpeg + LM Studio) et le
stockage local, tels que décrits dans `docs/DEPLOYMENT.md`.

Ce design **ajoute une couche devant** l'existant, il ne remplace rien.
Tailscale est rétrogradé en accès admin de secours.

## Décisions prises

| Question | Décision |
|---|---|
| Mode d'exposition | Cloudflare Tunnel (plutôt que port ouvert + Caddy, ou VPS relais) |
| Domaine | Domaine Dancing Dead existant (acheté chez OVH, site hébergé O2Switch) → sous-domaine `usine.` |
| DNS | Migration de la zone DNS vers Cloudflare (gratuit ; le domaine reste chez OVH, le site reste chez O2Switch) |
| Double porte d'auth | **Oui** : Cloudflare Access (code par email, liste d'emails autorisés) devant le login membre de l'app |
| Fichiers > 100 Mo | Déposés directement sur la tour (session locale / réseau local du bureau) — la limite d'upload du plan Free ne gêne pas |
| Coût | 0 € récurrent nouveau (plan Free Cloudflare, ≤ 50 users Access ; domaine déjà payé chez OVH) |

## Architecture

```
Membre (navigateur)
   │  https://usine.<domaine-dd>
   ▼
Cloudflare  — DNS + HTTPS + Access (email + code à usage unique)
   │  tunnel chiffré, connexion SORTANTE depuis la tour (aucun port ouvert)
   ▼
Tour Windows — cloudflared (service Windows, démarre au boot)
   │  http://localhost:8765
   ▼
waitress/Flask (serve.py) — SQLite + médias — LM Studio :1234 — FFmpeg
```

## Composants

### 1. Migration DNS OVH → Cloudflare (opération manuelle, une fois)

1. Compte Cloudflare gratuit → « Add site » avec le domaine DD.
2. Cloudflare scanne et recopie la zone existante (A/CNAME du site O2Switch,
   **MX des mails**, TXT/SPF…) — vérifier ligne par ligne avant de valider.
3. Chez OVH : remplacer les serveurs DNS par ceux fournis par Cloudflare.
4. Propagation en quelques heures ; site et mails continuent de fonctionner
   à l'identique.

**Risque principal du projet : un enregistrement oublié (surtout les MX).**
Parade : capture complète de la zone OVH avant migration.

### 2. Tunnel `cloudflared` (sur la tour)

- `winget install cloudflare.cloudflared`, `cloudflared tunnel login`,
  tunnel nommé `dd-usine`.
- Route publique : `usine.<domaine-dd>` → `http://localhost:8765`.
- Installé en **service Windows** (`cloudflared service install`) : démarre
  au boot avant le logon, se reconnecte seul.

### 3. Cloudflare Access (Zero Trust, gratuit ≤ 50 users)

- Application `usine.<domaine-dd>` avec policy « liste d'emails autorisés »
  (emails des membres, gérés à la main dans le dashboard Cloudflare).
- Méthode : **one-time PIN par email** — pas de compte Cloudflare à créer
  côté membres.
- Session Access : 30 jours par appareil.
- Départ d'un membre : retirer son email de la liste = accès coupé, même
  s'il connaît encore un login de l'app.

### 4. Durcissement minimal de l'app (seul lot de code)

Dans `create_app` (`webui.py`) :

- Cookies de session `Secure` + `HttpOnly` + `SameSite=Lax`.
- `ProxyFix` (werkzeug) pour que Flask voie le bon schéma/host derrière le
  proxy (`X-Forwarded-Proto/Host`).

Rien d'autre : pas de rate-limiting maison, pas de 2FA maison — Access s'en
charge (YAGNI). Testé via le test_client existant.

**Point d'attention `Secure`** : le cookie `Secure` n'est pas envoyé en HTTP
pur, or l'accès local à `http://localhost:8765` (dépannage sur la tour) doit
rester possible. Décision : le flag est piloté par la variable d'environnement
`PUBLIC_HTTPS=1` (posée dans le `.env` de la tour) ; absente en dev/local,
le cookie reste non-`Secure`. `HttpOnly` et `SameSite=Lax` sont, eux,
inconditionnels.

### 5. Stockage, sauvegarde, LLM — inchangés

- Stockage sur le disque de la tour ; le gros du volume = clips d'entrée.
- `backup.ps1` quotidien vers un autre disque/NAS (exigence non négociable :
  un seul disque = tout perdu en cas de panne).
- LM Studio local (GPU/VRAM pour le LLM ; le rendu FFmpeg, lui, est CPU).
- Bande passante : la génération n'en consomme pas ; l'équipe qui visionne à
  distance consomme le débit **montant** du bureau (~10-40 Mo par vidéo, à
  valider au premier déploiement).

### 6. Documentation

`docs/DEPLOYMENT.md` : nouvelle section « Accès public (Cloudflare Tunnel +
Access) » remplaçant l'actuelle section 6 « Accès équipe » (Tailscale devient
accès admin de secours) ; checklist post-reboot complétée (service
cloudflared actif, URL publique répond, porte Access opérationnelle).

Comme le reste du runbook : **non testable depuis le Mac de dev** — à valider
sur la tour au premier déploiement.

### 7. Contraintes « PC de Kévin » (à valider avec lui)

1. **Disponibilité** : l'usine n'est accessible que PC allumé — décider si
   extinction le soir est acceptable.
2. **Confort** : un lot de N variantes met le CPU à 100 % plusieurs minutes.
3. **Compte dédié** : compte Windows séparé pour l'usine (auto-login + tâches
   planifiées du runbook), distinct de sa session perso.

## Hors périmètre

- Publication automatique (décision 2026-07-08, inchangée).
- Levée de la limite d'upload 100 Mo (les plans payants ne la lèvent
  quasiment pas ; le dépôt local suffit).
- VPS / hébergement cloud (coût du rendu vidéo + GPU injustifié).
