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
- **Accents** : RGB split à l'impact, micro-glitch sur les temps forts ;
- **Punchlines incrustées** : sous-titres générés par Claude à partir d'un
  pré-prompt (ex. « motivation gym »), une phrase par coupe, incrustées en
  bas de l'image.

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
liens YouTube et téléchargement des sons, upload de tracks, gestion des
niches, lancement d'un lot de vidéos par niche avec journal en direct,
bibliothèque des vidéos produites (lecture, validation/rejet, téléchargement)
et réglages du montage (effets, accents, cadrage, rythme — persistés dans
`settings.json` et pris en compte par tous les rendus).

> **Pas de publication automatique.** La sortie de l'usine est une
> bibliothèque de vidéos à télécharger et poster à la main (décision du
> 2026-07-08). Aucune connexion à l'API TikTok.

## Plateforme d'équipe (phase 1)

L'interface est protégée par un login par membre. Crée les comptes en ligne
de commande (aucune page d'inscription) :

```bash
uv run python db.py add-member theo      # demande le mot de passe (masqué)
uv run python db.py list-members
```

Puis, dans l'interface, une **niche** (ex. « Naruto Édits », « Motivation
Gym ») relie tout ce qu'il faut pour produire :

- un **preset** de montage (le style),
- une **banque de clips** propre (upload direct ou liens YouTube téléchargés
  en vidéo ≤1080p) — dossier `data/niches/<slug>/clips/`,
- une **sélection de sons** dans le catalogue partagé (`tracks/`),
- un **préprompt de punchlines** (les sous-titres générés par Claude),
- une légende et des hashtags.

Depuis la carte **Génération**, on lance à la demande (aucune heure à
programmer) un **lot de N variantes** : chacune tire un son et une seed
distincts → montage ET punchlines différents. Les vidéos produites atterrissent
dans la **bibliothèque** de la niche (`data/niches/<slug>/videos/`), où on les
lit, les valide ou les rejette, et on les télécharge pour les poster à la main.

Les **presets** sont des styles de montage nommés (« strobo hard », « posé »,
« reels clean »…) réutilisables entre niches. Un preset ne stocke que ses
écarts par rapport aux réglages par défaut ; ordre de fusion :
`DEFAULT_CONFIG ← settings.json ← preset`.

Les punchlines nécessitent une clé `ANTHROPIC_API_KEY` (dans `.env`, lue
automatiquement) ; sans elle, la génération continue sans sous-titres.

L'état vit dans `platform.db` (SQLite) et `data/` — tous deux locaux,
jamais commités. Les mots de passe sont hachés (werkzeug).

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

### 2. Générer un lot de variantes pour une niche

Normalement lancé depuis l'interface (bouton **Générer**), mais accessible en
ligne de commande :

```bash
uv run python generate_niche.py <niche_id> <count>
```

Produit `count` variantes pour la niche : chacune tire un son (parmi ceux
sélectionnés dans la niche) et une seed distincts, ce qui donne un montage ET
des punchlines différents. Les presets liés à la niche sont alternés. Les
vidéos sont enregistrées en base (statut `proposed`) et déposées dans
`data/niches/<slug>/videos/`, prêtes à être passées en revue dans la
bibliothèque.

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
