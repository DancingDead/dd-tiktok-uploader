# Design — beatsync : montage vidéo vertical synchronisé sur les beats

Date : 2026-07-03 — validé en session avec Théo.

## Objectif

Prouver le maillon central de l'« usine à vidéos » du label : un morceau + un
dossier de rushes (extraits d'animé 16:9, longs) → un montage vertical 9:16
(1080x1920, H.264) dont les coupes tombent sur les beats. Sortie par défaut :
un extrait court (~30 s) prêt pour TikTok.

Hors périmètre : publication, multi-comptes, scraping, file de jobs,
détection de drop automatique (viendra plus tard).

## Arborescence

```
beatsync.py              # script unique, 5 fonctions indépendantes
tests/test_build_edl.py  # pytest — logique pure uniquement
requirements.txt
README.md
```

## Fonctions

```python
def analyze_audio(track_path: Path) -> dict
# {"duration", "bpm", "beats" (np.ndarray, s), "energy" (RMS), "energy_times"}

def load_clips(folder: Path) -> list[dict]
# [{"path", "duration", "width", "height", "ratio"}] — via ffprobe, trié par nom

def build_edl(analysis: dict, clips: list[dict], config: dict, seed: int) -> list[dict]
# PURE. [{"timeline_start", "duration", "clip_path", "clip_in", "beat_index"}]

def render(edl, audio_path: Path, output_path: Path, config: dict) -> None
# FFmpeg subprocess, stratégie « segments + concat »

def main() -> None
# CLI : track, clips_dir, -o, --seed, --start, --duration, --cut-every
```

## Config

```python
DEFAULT_CONFIG = {
    "width": 1080, "height": 1920, "fps": 30,
    "cut_mode": "energy",              # "energy" | "fixed"
    "cut_every": 2,                    # si cut_mode == "fixed"
    "energy_thresholds": (0.40, 0.75), # percentiles bas / haut
    "energy_intervals": (4, 2, 1),     # beats par coupe : calme / moyen / intense
    "start": 0.0, "end": 30.0,         # fenêtre (résolue dans main)
    "crf": 20, "preset": "medium", "audio_bitrate": "192k",
}
```

## Décisions et justifications

1. **Coupes pilotées par l'énergie** (choix brainstorm) : l'énergie RMS de
   chaque beat est convertie en rang percentile **sur le morceau entier**
   (pas la fenêtre) — 30 s de pur drop ⇒ mitraillage partout, comportement
   voulu. On avance beat par beat : l'énergie au cut courant décide de
   l'intervalle jusqu'au suivant. `--cut-every N` force le mode fixe.
2. **Quantification frame-grid dans build_edl** : les timestamps de cut sont
   arrondis à la grille de frames (30 fps) puis les durées déduites par
   différence ⇒ erreur ≤ ½ frame par cut, jamais cumulative. Dans la logique
   pure pour être testé.
3. **Reproductibilité** : clips triés par nom, `random.Random(seed)` local,
   flags FFmpeg bitexact. Même seed ⇒ même EDL, fichier identique sur une
   même machine.
4. **Rendu « segments + concat »** (option B validée) : un fichier par
   segment (`-ss` avant `-i` = seek rapide, scale/crop/fps/x264 homogènes),
   puis concat demuxer en copie de flux + piste audio + `-shortest`.
   Frame-accurate, mémoire plate, déboguable segment par segment.
5. **subprocess, pas ffmpeg-python** : lib non maintenue ; les commandes
   brutes sont loggables et rejouables à la main.
6. **« Temps forts »** ≈ intervalle 4 beats (4/4 quasi systématique en
   électro) ; vraie détection de downbeat hors périmètre.
7. **Sélection des clips** : tirage seedé, pas deux fois le même rush
   d'affilée (si ≥ 2 utilisables), `clip_in` aléatoire tel que l'extrait
   tienne dans le clip. Crop central 9:16, audio des clips ignoré.

## Limitations connues (assumées V1)

- Rips d'animé avec letterbox incrusté : le crop central gardera les bandes.
- Phase des « temps forts » non garantie (premier beat détecté ≠ toujours le « 1 »).
- Sources 23,976 fps normalisées à 30 fps par duplication de frames.

## Tests (build_edl uniquement, aucun média requis)

- Même seed ⇒ EDL identique ; seeds différents ⇒ EDL différents.
- Segments contigus, sans trou ni chevauchement, couvrant la fenêtre.
- Cuts sur les beats à ±½ frame.
- `clip_in + duration ≤ durée du clip` pour chaque segment.
- Pas deux fois le même clip d'affilée.
- Énergie synthétique faible→forte ⇒ coupes plus denses dans la partie forte.

## Définition de « ça marche »

Bout en bout sur un morceau + dossier de clips ; coupes sur les beats
(tolérance ½ frame) ; sortie 9:16 1080x1920 ; audio synchronisé ; durée
correcte ; même seed ⇒ même vidéo.
