# Design — publication TikTok automatisée (usine à vidéos, partie 2)

Date : 2026-07-04 — brainstorm avec Théo. Décisions : 2-5 comptes du label,
API officielle TikTok, worker sur petit serveur, même catalogue décliné en
variantes par compte (seeds), review humaine au début puis auto post-audit.

## Architecture cible

```
tracks/ + clips/                        (existant)
    │ beatsync.py                       (existant)
    ▼
batch_generate.py ──► queue/pending/    (NOUVEAU)
    vidéo .mp4 + sidecar .json : {compte, heure_prévue, caption, hashtags,
    morceau, seed, statut}
    ▼   (rsync vers le serveur)
publisher.py — worker cron sur VPS      (NOUVEAU)
    toutes les ~10 min : publie ce qui est dû via Content Posting API
    phase brouillon : upload inbox → Théo valide dans l'app (2 s/post)
    phase directe (post-audit) : publication programmée réelle
    → queue/posted/ + journal
tokens/ — OAuth par compte (access 24 h, refresh ~1 an), jamais dans git
```

## Décisions et raisons

1. **API officielle (Content Posting API)** — durable, multi-comptes propre
   (un OAuth par compte), zéro risque de ban. L'automatisation navigateur est
   écartée : contraire aux CGU, risque réel pour des comptes qui ont de la
   valeur. Limite connue : avant l'audit de l'app, la publication directe est
   restreinte (visibilité privée) — le mode **brouillon/inbox** marche dès le
   premier jour et impose de toute façon la review voulue au début.
2. **Variantes par seed et par compte** — même morceau, seed différente =
   vidéo différente (déjà gratuit dans beatsync). Évite la déduplication
   TikTok entre comptes et donne un A/B naturel.
3. **File = dossiers + sidecars JSON** — pas de base de données à ce stade.
   `pending/` → `posted/` (ou `failed/`), l'état est lisible à l'œil nu.
4. **Génération locale, publication serveur** — le Mac génère (CPU costaud),
   le VPS (~5 €/mois) ne fait que poster : léger, allumé 24/7, tokens isolés.
5. **Garde-fous anti-spam dès le départ** — plafond de posts/jour/compte,
   espacement minimal, jitter sur les heures (pas de métronome).

## Roadmap

- **A — Générateur en lot + file** (aucune dépendance TikTok, buildable
  immédiatement) : plan de publication simple (morceaux × comptes ×
  créneaux), captions par templates, sidecars JSON.
- **B — App TikTok + brouillons sur 1 compte** : Théo crée l'app sur
  developers.tiktok.com (Login Kit + Content Posting API) ; OAuth + upload
  inbox ; validation dans l'app.
- **C — Serveur + cron + 2-5 comptes** : OAuth de chaque compte, worker,
  sync de la file.
- **D — Audit TikTok → publication directe** planifiée, review optionnelle.
- **E — Boucle analytics** (plus tard) : performances par seed/fenêtre/compte
  pour informer la génération.

Nota : le mode brouillon peut suffire longtemps — valider 2 brouillons/jour
sur 5 comptes prend deux minutes dans l'app, et l'audit TikTok (durée et
issue incertaines pour un outil interne) n'est alors pas bloquant.

## Prérequis côté Théo (seul à pouvoir le faire)

1. Compte développeur sur developers.tiktok.com, créer une app, activer
   **Login Kit** + **Content Posting API**, définir une redirect URL.
2. Passer les comptes du label en Business/Creator si besoin ; chaque compte
   autorisera l'app via OAuth au moment de l'étape B/C.
