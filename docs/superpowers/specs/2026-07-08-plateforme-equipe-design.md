# Design — plateforme d'équipe : usine à contenus par niches

Date : 2026-07-08 — brainstorm avec Théo. Remplace l'objectif de publication
automatique de la spec `2026-07-04-publication-tiktok-design.md` :
**la publication est ABANDONNÉE** (décision explicite de Théo). La plateforme
se concentre sur la CRÉATION automatique ; l'équipe poste elle-même
(TikTok, Reels — même format 9:16).

## Décisions du brainstorm

1. **Niche** = un univers visuel (banque de clips propre, templates de
   légendes/hashtags) ; la musique vient TOUJOURS du catalogue Dancing Dead —
   la plateforme reste une machine à promouvoir le label.
2. **Workflow « usine + validation »** : génération automatique au rythme de
   chaque niche ; les membres approuvent/rejettent/re-génèrent depuis un feed.
3. **Équipe 2-4 personnes, toutes égales** : login simple par membre, tout le
   monde voit tout, niches attribuées par convention.
4. **VPS tout-en-un** (~8-15 €/mois, Hetzner/Scaleway) : dashboard +
   génération 24/7, HTTPS (Caddy), accessible par URL à l'équipe.
5. **Pas de publication automatique** : la sortie de l'usine est une
   bibliothèque de vidéos prêtes à télécharger et poster à la main.
   (Les acquis OAuth TikTok — app sandbox, tiktok_auth.py, site de callback —
   sont conservés en sommeil, ne pas les re-proposer sans demande.)

## Modèle de données (SQLite, `platform.db`)

- `members` : name, password_hash.
- `presets` : name + surcharges de montage (mêmes clés que settings.json :
  effets, accents, durée, rythme des coupes, min_presence…). Des styles
  nommés — ex. « strobo hard », « posé », « reels clean » — réutilisables
  entre niches. Répond au besoin « différents paramètres de montage et
  d'édition pour les Reels ». Ordre de fusion d'une génération :
  DEFAULT_CONFIG ← settings.json (défauts plateforme) ← preset de la vidéo.
- `niches` : name, slug, owner (informatif), cadence (vidéos/jour),
  presets liés (1..n — l'usine alterne), caption_template, hashtags.
- `videos` : niche, preset, track, seed, fichier, statut
  (`proposed` → `approved` | `rejected` ; `approved` → `downloaded`/`posted`
  marqué à la main pour le suivi), created_at, caption générée.

## Subtitles générés (par niche, optionnel)

Certaines niches affichent des punchlines incrustées (« subtitles » au sens
edit TikTok : hook + punch, pas de la transcription). Ajouté en review de
spec par Théo. NB : le titre incrusté V4 avait été retiré (« passait mal ») —
cette fois le style est soigné et PAR NICHE.

- Config niche : `subtitles: {enabled, preprompt, style}` — le pré-prompt
  décrit le ton/thème (ex. « punchlines sombres sur le dépassement, style
  edit Naruto, français, 6 mots max »). Style : position, taille, casse,
  contour.
- Génération à l'usine : appel Claude API (`claude-opus-4-8`, sortie JSON
  structurée `{hook, punch}` via output_config). La seed de la vidéo varie le
  prompt (« variation n°<seed> ») pour la diversité. IMPORTANT : pas de graine
  d'échantillonnage LLM — le texte généré est STOCKÉ avec la vidéo
  (reproductibilité par persistance) ; re-génération = nouveau texte.
- Rendu : drawtext par segment (l'architecture segment-par-segment le permet) —
  hook pendant le buildup, punch à partir du drop. Validation visuelle frame
  par frame obligatoire avant de généraliser.
- Échec de l'appel API → vidéo générée SANS texte + avertissement (l'usine ne
  bloque jamais sur le LLM).
- Coût : ~0,005 €/vidéo (négligeable). Requiert `ANTHROPIC_API_KEY` dans .env.

## Stockage fichiers

- `tracks/` : catalogue DD partagé (inchangé, alimenté par liens YouTube/upload).
- `data/niches/<slug>/clips/` : banque de clips de la niche (upload + liens
  YouTube par niche). Scan par niche AVEC CACHE (résultats par fichier,
  invalidé par mtime) — on ne re-scanne pas 30 clips à chaque vidéo.
- `data/niches/<slug>/videos/` : les vidéos produites.

## L'usine (worker de génération)

Boucle continue sur le VPS : pour chaque niche sous son quota du jour →
choisit un morceau (rotation sur le catalogue, pas deux fois le même avant
d'avoir tout couvert), choisit le preset (alternance), seed dérivée
(niche, morceau, date, preset) → génère → statut `proposed`.
Générations sérialisées (une à la fois, le VPS n'est pas une ferme).
Échec de génération = vidéo `failed` visible au dashboard avec le log.

## Dashboard (évolution de webui.py)

- **Login** simple par membre (session cookie).
- **Feed « à valider »** : lecteur vidéo intégré, Approuver / Rejeter /
  Re-générer (nouvelle seed). Filtre par niche.
- **Bibliothèque** : vidéos approuvées, téléchargement en un clic, légende
  copiable, bouton « marquée comme postée ».
- **Mes niches** : CRUD niche, banque de clips (upload + liens YouTube),
  cadence, presets, templates.
- **Presets** : éditeur des styles de montage (les réglages actuels, nommés).
- **Catalogue** : les onglets actuels liens/tracks (partagés).

## Ce qui ne change pas

Pipeline de montage complet (beatsync : beats, drop, scan, EDL, effets,
rendu), TDD sur la logique pure, reproductibilité par seed.

## Phases

1. **Fondations** : SQLite + login + niches + presets + banques par niche
   (+ cache de scan). Utilisable en local.
2. **Usine** : worker de génération, feed de validation, bibliothèque.
   L'usine tourne sur le Mac de Théo.
3. **VPS** : déploiement (Caddy HTTPS, systemd, sauvegardes quotidiennes
   de platform.db), onboarding équipe.

## Hors périmètre (explicite)

Publication automatique TikTok/Reels, statistiques de performance,
rôles/permissions, file multi-serveurs.
