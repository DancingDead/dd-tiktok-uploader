# beatsync.py — vue d'ensemble

> Fiche 00 · les 6 sous-systèmes et ce qui les relie.
> Détail : [01](01-analyse-audio.md) · [02](02-scan-clips.md) · [03](03-cadrage.md) · [04](04-build-edl.md) · [05](05-punchlines.md) · [06](06-rendu.md)
> Voir aussi : [10 — clipper](10-clipper.md), un **sous-système parallèle** —
> vidéo longue parlée → shorts classés, pas une étape de ce pipeline.

## L'idée en une phrase

Un morceau + un dossier de clips → une vidéo verticale dont les coupes tombent
sur les beats. 1557 lignes, ~50 fonctions, un seul fichier.

## Pourquoi un seul fichier ?

Parce que le découpage naturel n'était pas évident au départ, et qu'un fichier
unique évite les imports circulaires pendant qu'on cherche. Le fichier est
**écrit pour être éclaté plus tard** : chaque sous-système ne parle aux autres
que par des dicts (`analysis`, `clips`, `config`, `edl`). Le jour où on scinde,
il n'y a pas de refactor à faire — juste des `import`.

## Les 6 sous-systèmes

```
                        ┌─────────────────────────────────────┐
   morceau .mp3 ───────▶│ 1. ANALYSE AUDIO        l.245-702    │
                        │    analyze_audio, find_drop,        │
                        │    find_calm                        │
                        └──────────────┬──────────────────────┘
                                       │ analysis = {beats, bpm, energy}
                                       ▼
                        ┌─────────────────────────────────────┐
   dossier clips ──────▶│ 2. SCAN DES CLIPS       l.261-629    │
                        │    load_clips, scan_clips,          │
                        │    classify_frames, usable_intervals│
                        └──────────────┬──────────────────────┘
                                       │ clips = [{path, intervals, interest_x, dual}]
                                       ▼
                        ┌─────────────────────────────────────┐
                        │ 3. CADRAGE              l.704-785    │
                        │    resolve_window, apply_format,    │
                        │    snap_end_to_phrase, frame_extract│
                        └──────────────┬──────────────────────┘
                                       │ config{start, end, drop_time, width, height}
                                       ▼
                        ┌─────────────────────────────────────┐
                        │ 4. BUILD_EDL  ★ LE CŒUR  l.787-1250  │
                        │    100 % pur, aucun I/O             │
                        └──────────────┬──────────────────────┘
                                       │ edl = [{clip_path, clip_in, duration, effects…}]
                                       ▼
                        ┌─────────────────────────────────────┐
                        │ 5. PUNCHLINES          l.1252-1491   │
                        │    assign_caption_slots, LLM,       │
                        │    apply_subtitles                  │
                        └──────────────┬──────────────────────┘
                                       │ edl enrichi de `caption`
                                       ▼
                        ┌─────────────────────────────────────┐
                        │ 6. RENDU              l.1524-1807    │
                        │    _segment_filters, render         │
                        └──────────────┬──────────────────────┘
                                       ▼
                                  output.mp4
```

`generate_video()` (l.1224) est le chef d'orchestre qui enchaîne 1→6.
`main()` (l.1507) n'est que l'habillage CLI par-dessus.

## La ligne de partage qui structure tout

**Décision d'un côté, exécution de l'autre.**

| | Sous-systèmes | Nature |
|---|---|---|
| **Décide** | 1, 3, 4, 5 (slots) | Pur : entrées → sorties, aucun effet de bord |
| **Exécute** | 2 (FFmpeg), 5 (LLM), 6 (FFmpeg) | I/O, réseau, sous-processus |

`build_edl` — la fonction la plus complexe du projet — ne touche **jamais** à un
pixel. Elle prend des dicts et retourne une liste de dicts. C'est pour ça
qu'elle est testable en millisecondes, sans FFmpeg ni fichier vidéo.

Corollaire pratique : quand tu veux comprendre ou modifier une décision de
montage, tu n'as qu'un seul endroit à regarder, et tu peux la vérifier sans
jamais lancer un rendu.

## Le contrat de reproductibilité

**Même seed = même vidéo, au bit près.** Trois garde-fous, dans trois
sous-systèmes différents :

| # | Où | Quoi | Sans ça |
|---|---|---|---|
| 1 | `load_clips` l.264 | `sorted(Path(folder).iterdir())` | L'ordre du filesystem varie → le tirage change |
| 2 | `build_edl` l.828 | `rng = random.Random(seed)`, jamais le RNG global | Un appel `random` ailleurs décale tout |
| 3 | `render` l.1788 | `-bitexact -map_metadata -1` | Horodatage dans le fichier → octets différents |

Un quatrième invariant, moins visible mais aussi important :

> **Les timestamps sont quantifiés sur la grille de frames dans `build_edl`,
> pas dans `render`.**

Si on arrondissait au rendu, l'erreur de chaque segment s'ajouterait à la
suivante, et sur 30 coupes la vidéo dériverait de l'audio. En quantifiant à la
décision, l'erreur est bornée à ½ frame **par coupe**, jamais cumulée. Et comme
c'est dans la partie pure, c'est testable.

## Le principe « ne jamais bloquer l'usine »

Un lot de 10 vidéos ne doit pas mourir sur un incident. On dégrade au lieu de
lever :

| Situation | Comportement |
|---|---|
| Énergie du morceau plate | `find_drop` → `None`, cadrage normal ([01](01-analyse-audio.md)) |
| Aucune scène de fin exploitable | `find_final_scene` → `None`, montage classique ([04](04-build-edl.md)) |
| Catalogue épuisé (anti-répétition) | on rouvre les plages déjà montrées plutôt que d'échouer ([04](04-build-edl.md)) |
| Bandes noires douteuses (scène sombre) | `content_rect` → `None`, aucun rognage ([02](02-scan-clips.md)) |
| LLM éteint / sans clé | `generate_punchlines` → `[]`, vidéo sans texte ([05](05-punchlines.md)) |
| Cache corrompu **ou de schéma périmé** | traité comme un miss, on re-scanne ([02](02-scan-clips.md)) |
| Format inconnu | retombe sur vertical (`apply_format` l.109) |
| Police absente | repli sur les polices système ([05](05-punchlines.md)) |

Seule exception : **`build_edl` lève** s'il n'y a aucun clip exploitable
(l.876). Là, il n'y a rien à dégrader — on ne peut pas monter une vidéo sans
image. Le message explique les trois causes possibles.

## Où lire quoi

| Tu veux… | Va voir |
|---|---|
| comprendre le rythme des coupes | [04 — build_edl](04-build-edl.md) |
| savoir pourquoi un clip n'est jamais utilisé | [02 — scan](02-scan-clips.md) |
| régler la fenêtre / le drop | [01](01-analyse-audio.md) + [03](03-cadrage.md) |
| toucher aux effets visuels | [06 — rendu](06-rendu.md) |
| changer de modèle LLM | [05 — punchlines](05-punchlines.md) |
| découper une vidéo longue en shorts (pas du montage aux beats) | [10 — clipper](10-clipper.md) |

## Ce qui n'est pas dans beatsync.py

Volontairement : la base SQLite (`db.py`), l'interface (`webui.py`), la boucle
de lot (`generate_niche.py`), le téléchargement (`fetch_tracks.py`), et le
clipper (`clipper.py`/`clip_source.py`/`speaker.py`, [fiche 10](10-clipper.md)) — un second
front de l'usine qui ne partage ni musique ni EDL avec ce pipeline.

`beatsync.py` est utilisable **seul**, en CLI, sans base ni serveur :

```bash
uv run python beatsync.py tracks/morceau.mp3 clips/ -o out.mp4 --seed 42
```

C'est le socle. Tout le reste est de la couche par-dessus.
