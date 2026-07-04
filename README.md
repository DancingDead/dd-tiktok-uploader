# beatsync — montage vidéo synchronisé sur les beats

Transforme un morceau + un dossier de clips en un montage vertical **9:16
(1080x1920, H.264)** dont les coupes tombent sur les beats de la musique,
façon edit animé TikTok :

- **Drop auto** : le script détecte le drop et cadre la fenêtre dessus
  (10 s de buildup + 20 s de drop par défaut) ;
- **Coupes pilotées par l'énergie** : plans posés sur le buildup, strobo au drop ;
- **Effets** : punch-zoom, flash blanc à l'impact, shake, slow-mo juste avant le drop ;
- **Scan des clips** : cartes Crunchyroll (fond orange), frames noires,
  passages statiques et pans d'établissement sans intérêt sont écartés ;
- **Personnages à l'écran** : détection de visages animé (OpenCV +
  lbpcascade_animeface, fourni dans `assets/`) + score de contours dans la
  bande centrale — les plans vides sont exclus ;
- **Cadrage intelligent** : la fenêtre 9:16 se cale sur les personnages
  détectés ; duel frontal → split-screen haut/bas ; action sur toute la
  largeur → plan entier sur fond flouté ; logo de chaîne gommé (delogo) ;
- **Chronologie** : les extraits avancent dans l'histoire du clip au rythme
  de la vidéo — le drop tombe sur le climax du clip ;
- **Fin musicale** : la vidéo s'étend jusqu'à la fin de phrase (16 beats)
  suivante — la musique ne coupe jamais en plein milieu ;
- **Accents** : RGB split à l'impact, micro-glitch sur les temps forts.

## Prérequis

1. **Python ≥ 3.11**
2. **FFmpeg** (dépendance *système*, inclut `ffprobe`) :

   ```bash
   # macOS
   brew install ffmpeg
   # Debian / Ubuntu
   sudo apt install ffmpeg
   ```

## Installation

Avec [uv](https://docs.astral.sh/uv/) (recommandé) :

```bash
uv sync
```

Ou avec pip classique :

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Interface graphique

```bash
uv run python webui.py        # puis ouvre http://127.0.0.1:8765
```

Tout se pilote depuis le navigateur (local uniquement, rien d'exposé) :
liens YouTube et téléchargement des sons, upload de tracks, connexion des
comptes TikTok (OAuth), édition du plan de publication, génération des
vidéos avec journal en direct, gestion de la file d'attente, et réglages du
montage (effets, accents, cadrage, rythme — persistés dans `settings.json`
et pris en compte par tous les rendus).

## Utilisation en ligne de commande

### 1. Récupérer les morceaux depuis YouTube (optionnel)

Mets un lien YouTube par ligne dans `links.txt` (vidéo ou playlist, `#` pour
commenter), puis :

```bash
uv run python fetch_tracks.py            # les mp3 arrivent dans tracks/
uv run python fetch_tracks.py autre_liste.txt --dest autre_dossier
```

Relancer le script ne retélécharge pas ce qui existe déjà, et un lien mort ne
bloque pas les suivants.

### 2. Générer en lot pour la publication (optionnel)

Copie `plan.example.toml` vers `plan.toml` (morceaux × comptes × créneaux),
puis :

```bash
uv run python batch_generate.py
```

Chaque post est décliné en **une vidéo différente par compte** (seed dérivée
du trio morceau/compte/date — pas de doublon entre comptes) et déposé dans
`queue/pending/` avec ses métadonnées de publication (compte, heure avec
jitter, légende, hashtags). Relancer ne re-rend pas ce qui existe déjà.
Cette file sera consommée par le worker de publication TikTok (voir
`docs/superpowers/specs/2026-07-04-publication-tiktok-design.md`).

### 3. Monter une vidéo à l'unité

```bash
uv run python beatsync.py tracks/morceau.mp3 ./clips -o output/sortie.mp4 --seed 42
```

| Argument | Défaut | Rôle |
|---|---|---|
| `track` | — | chemin du morceau audio (wav, mp3…) |
| `clips_dir` | — | dossier des clips vidéo (mp4, mov, mkv, webm…) |
| `-o, --output` | `output.mp4` | fichier de sortie |
| `--seed` | `42` | graine : même seed ⇒ exactement la même vidéo |
| `--start` | *(drop auto)* | début manuel de la fenêtre (s) ; sans lui, cadrage auto sur le drop |
| `--duration` | `30` | durée de la fenêtre (s), ou `full` pour tout le morceau |
| `--cut-every N` | *(mode énergie)* | force une coupe tous les N beats (1 = strobo, 4 ≈ temps forts) |

## Fonctionnement

1. `analyze_audio` — librosa extrait la grille de beats, le BPM et l'enveloppe RMS.
2. `load_clips` — ffprobe lit durée/résolution des clips (triés par nom).
3. `build_edl` — logique pure : choisit quel extrait de quel clip couvre quel
   beat. L'énergie de chaque beat (percentile sur le morceau entier) fixe
   l'intervalle entre coupes (4 / 2 / 1 beats). Timestamps quantifiés sur la
   grille de frames — pas de dérive cumulative.
4. `render` — FFmpeg encode un segment par coupe (crop central 9:16, 30 fps,
   nombre de frames exact) puis concatène en copie de flux avec la piste audio.

## Tests

```bash
uv run pytest
```

## Limitations connues (V1)

- Clips avec bandes noires incrustées : le crop central les conserve.
- « Temps forts » approximés (1 beat sur 4) — pas de vraie détection de downbeat.
- La reproductibilité à l'octet près est garantie sur une même machine.
