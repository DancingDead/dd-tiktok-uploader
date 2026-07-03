# Design V2 — dynamique « edit animé », drop auto, scan des clips

Date : 2026-07-04 — validé en session avec Théo (retours sur le premier montage réel).
Complète `2026-07-03-beatsync-design.md`.

## Retours à l'origine

1. Montage pas assez dynamique vs les edits animé trendy sur TikTok.
2. Trouver buildup + drop automatiquement pour cadrer la fenêtre (montée → impact).
3. Extraits parfois inutilisables : cartes Crunchyroll (fond orange), à écarter.

## #2 — find_drop (pure, testée)

`find_drop(analysis, config) -> float | None`
Énergie lissée (~2 s), contraste avant/après en fenêtres glissantes ±8 s ;
le max de contraste = drop, calé sur le beat le plus proche. Retourne None si
l'énergie est plate (contraste max < 20 % de l'amplitude). Dans `main` :
sans `--start`, fenêtre = `[drop - 10 s buildup, drop + 20 s]` (bornée au
morceau) ; fallback début du morceau si None. `--start N` = mode manuel.

## #1 — Effets par segment (EDL enrichie, rendu par segment)

Tous validés par Théo : punch-zoom, flash blanc, shake, speed-ramp.
`build_edl` annote chaque entrée : `section` ("buildup"/"drop"), `speed`,
`effects` (liste). Règles déterministes (seed) :

- **Strobo au drop** : coupes forcées à 1 beat sur `[drop, drop + 16 beats)`.
- **Gasp** : le dernier segment avant le drop passe en slow-mo (speed 0.5).
- **Punch-zoom** : segments intenses + toute la section drop (zoompan dégressif ~6 frames).
- **Flash blanc** : premier segment du drop + un segment sur 8 en section drop (fade from white 3 frames).
- **Shake** : impact du drop + ~30 % des segments intenses (tirage seedé) ;
  crop jitter sinusoïdal déterministe (fonction de n, pas de RNG au rendu).
- **Bornes** : la source consommée = `duration × speed` → `clip_in + duration×speed ≤ durée du clip`.

Le rendu reste « un segment = un fichier » : le filtre vf est construit par
segment selon `effects`, `-frames:v` garantit toujours le compte exact.

## #3 — scan_clips (I/O fin, cœur pur testé)

Échantillonnage `fps=2, scale=32x18, rgb24` via FFmpeg → numpy (aucune dépendance en plus).

- `classify_frames(frames, dt)` (pure) : masque orange Crunchyroll
  (R>150, 60<G<180, B<100, R>G>B, >35 % des pixels), frames noires
  (moyenne <18), score de mouvement (diff moyenne inter-frames).
- `usable_intervals(...)` (pure) : plages contiguës utilisables (ni orange,
  ni noir, ni statique), marge 0.5 s, longueur min 1 s, avec mouvement moyen.
- `build_edl` ne pioche `clip_in` que dans ces plages ; les segments
  intenses/drop préfèrent les plages à fort mouvement (≥ médiane).
- Clip sans scan : plage unique `[0, durée]` (compat tests).

## Vérification

- Tests purs : find_drop (marche d'énergie synthétique), classification
  (frames synthétiques orange/noir/statiques), intervalles, EDL v2
  (strobo post-drop, gasp, bornes×speed, sections).
- E2E réel : scan du clip Naruto → vérifier visuellement (frames extraites)
  que les cartes orange sont exclues ; montage complet Virus V4 + frames
  aux cuts pour contrôler zoom/flash.
