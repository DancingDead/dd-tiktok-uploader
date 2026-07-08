# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

Cœur d'une « usine à vidéos » pour un label de musique électronique (Dancing Dead Records) : transformer un morceau du label + un dossier de clips en un montage vidéo vertical 9:16 (1080x1920, H.264) dont les coupes sont synchronisées sur les beats de la musique.

Périmètre volontairement minimal à ce stade : pas de publication, pas de multi-comptes, pas de scraping, pas de file de jobs. Éviter toute sur-ingénierie.

## Environnement et commandes

Le projet utilise **uv** (pas pip directement — le venv n'a pas de module pip).

```bash
uv sync                          # installer les dépendances
uv run python beatsync.py ...    # lancer le script principal
uv run pytest                    # lancer les tests
uv run pytest tests/test_build_edl.py -k <nom>   # un seul test
```

**FFmpeg est une dépendance système** (`brew install ffmpeg` sur macOS). Le rendu passe par `subprocess` (jamais par ffmpeg-python, non maintenu) ; `ffprobe` sert à lire les métadonnées des clips.

## Architecture

Scripts indépendants :

- `db.py` — persistance SQLite de la plateforme d'équipe (`platform.db`, gitignoré) : tables members/presets/niches/videos. Toutes les fonctions sont pures et testées (`connect`, `slugify` avec translittération NFKD des accents, membres avec mots de passe hachés werkzeug + CLI `add-member`/`list-members`, presets + `effective_config` = `DEFAULT_CONFIG ← settings.json ← preset`, niches avec dossiers `data/niches/<slug>/clips/`, videos statut proposed|approved|rejected|posted|failed). CLI : `python db.py add-member <nom>`.
- `webui.py` — interface Flask locale (127.0.0.1:8765). `create_app(root=None)` injectable (tous les chemins dérivés de `root`, testable via test_client). Login par membre (session, `before_request` → 401 sur `/api/*`), secret persisté dans `data/secret_key` (0600). Onglets : Niches, Presets, Tracks, Liens, Plan & file, Réglages (CRUD niches/presets, upload clip + liens/téléchargement vidéo par niche). Identité visuelle Dancing Dead : noir OLED + accent rouge `#ff1e46`, typo Fira Sans/Code, icônes SVG (pas d'emoji). Défense XSS : `esc()` sur tout champ STATE rendu via innerHTML + coercion serveur des champs numériques (cadence via `db.update_niche`, overrides via `coerce_overrides`) → 400 si non convertible. Parties pures testées : `plan_to_toml`. `merge_settings`/`load_settings` vivent dans beatsync.
- **Publication TikTok abandonnée** (décision 2026-07-08) : plus de connexion à l'API TikTok pour la publication auto. `tiktok_auth.py`, l'onglet Comptes et les endpoints `/api/auth/*` ont été supprimés. La sortie de l'usine est une bibliothèque de vidéos à poster à la main. L'app sandbox TikTok et le site callback (repo Hooriiiii/dancingdead-site) restent en sommeil hors du repo.
- `fetch_tracks.py` — télécharge l'audio (mp3) OU la vidéo (≤1080p mp4, `--video`, pour les banques de clips par niche) des liens YouTube via yt-dlp. `parse_links` et `ytdlp_args` sont les parties pures testées.
- `batch_generate.py` — décline `plan.toml` (posts × comptes, tomllib stdlib) en vidéos + sidecars JSON dans `queue/pending/`, une seed dérivée par (morceau, compte, date) donc une variante par compte. Idempotent. Parties pures testées : `build_jobs`, `derive_seed`, `make_caption`, `schedule_time` (jitter déterministe 0-14 min), `output_stem`. La file sera consommée par le futur worker TikTok (spec `2026-07-04-publication-tiktok-design.md` : API officielle, brouillons d'abord).
- `beatsync.py` — le montage, découpé en fonctions indépendantes et testables, pensées pour être éclatées en modules plus tard :

- `analyze_audio(track_path)` — librosa : grille de beats (timestamps en s), BPM, enveloppe d'énergie RMS. Import librosa paresseux (coûteux, inutile pour la logique pure)
- `find_drop(analysis, config)` — **pure** : drop = max de contraste d'énergie avant/après (fenêtres ±8 s), calé sur un beat ; None si énergie plate. Sans `--start`, main cadre la fenêtre sur `[drop - buildup, drop + reste]`
- `load_clips(folder)` — métadonnées des clips via ffprobe ; liste **triée par nom** (déterminisme)
- `scan_clips(clips, cache_dir=None)` / `_scan_one` / `classify_frames` / `usable_intervals` / `_char_presence` — frames 640x360 à 2 fps via FFmpeg (miniatures 32x18 par blocs pour couleur/mouvement). Avec `cache_dir`, résultat par clip mis en cache (clé md5 du chemin, invalidé par mtime, tolérant à la corruption → cache miss) : on ne re-décode pas 30 clips à chaque génération → exclusion cartes orange Crunchyroll, frames noires, statiques, plages à mouvement moyen < 0.05 (pans d'établissement) et plans sans personnages. Présence = visage animé (cascade `assets/lbpcascade_animeface.xml`, poids 1.0) OU contours d'encre (poids 0.6), détectés dans la **bande centrale 40 %** seulement (ce qui survit au crop 9:16 — élimine le logo Crunchyroll du coin par construction). Présence 0 = plan vide → coupe la plage. `classify_frames`/`usable_intervals` sont **pures et testées**. `intervals: []` = clip scanné inutilisable (≠ clé absente = pas scanné, clip entier utilisable). OpenCV pinné `<5` (CascadeClassifier retiré en 5.x)
- `build_edl(analysis, clips, config, seed)` — **logique pure, sans I/O ni rendu** : construit l'Edit Decision List. Rythme « energy » : percentile d'énergie de chaque beat (sur le morceau ENTIER) → intervalle 4/2/1 beats ; `--cut-every N` force le mode fixe. Avec `drop_time` : coupe garantie pile sur le drop, strobo 1 beat pendant 16 beats, sections buildup/drop, gasp slow-mo x0.5 juste avant l'impact, effets par segment (`zoom`/`flash`/`shake` — déterministes à seed égal). `clip_in` pioché uniquement dans les plages exploitables, filtrées par `min_presence`. Mode `chrono` (défaut) : position dans la timeline ≈ position dans l'histoire du clip (mapping proportionnel sur les plages, monotone par clip, jitter seedé) — le drop tombe sur le climax ; `chrono: False` restaure le tirage libre avec préférence mouvement pour les segments intenses. Cadrage par extrait (fenêtre min 3 échantillons de scan) : `focus_x` (centroïde visages/contours) cale le crop 9:16 ; duel strict majoritaire → `layout: split` ; dispersion σ≥0.18 → `layout: blur`. Accents : `rgb` sur les impacts, `glitch` seedé sur ~25 % des segments intenses du drop. `snap_end_to_phrase` (pure) étend la fin au multiple de 16 beats après le drop — la musique ne coupe pas en pleine phrase
- `render(edl, audio_path, output_path, config)` — un segment encodé par entrée d'EDL (vf construit par segment selon les effets) puis concat en copie de flux. Chaque segment est forcé à un nombre de frames EXACT (`tpad` + `-frames:v`) : sans ça, les sources 23,976 fps rendent quelques ms de moins et le concat accumule une dérive audio/vidéo. La source consommée = `duration × speed`
- `main()` — CLI argparse ; sans `--start`, cadrage auto sur le drop (buildup 10 s + drop) ; fenêtre de 30 s par défaut (sortie format TikTok)

## Contrainte centrale : reproductibilité

Relancer avec la même seed doit produire exactement la même vidéo. Trois garde-fous à ne pas casser :

1. `load_clips` trie les fichiers par nom (l'ordre du filesystem n'est pas déterministe)
2. `build_edl` utilise un `random.Random(seed)` local, jamais le RNG global
3. Le rendu FFmpeg passe des flags `bitexact` pour neutraliser les métadonnées horodatées

Autre invariant : les timestamps de cut sont quantifiés sur la grille de frames (fps de la config) dans `build_edl`, pas dans `render`, pour que l'erreur ne s'accumule pas et que ce soit testable.
