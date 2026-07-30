"""beatsync — montage vidéo vertical synchronisé sur les beats d'un morceau.

Pipeline : analyze_audio -> load_clips -> build_edl (logique pure) -> render.
"""

import argparse
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
# Plafond de durée d'une image au montage : au-delà, un fixe casse le rythme.
# Constante de module, pas un réglage : le catalogue d'images ne s'expose pas.
IMAGE_MAX_DUR = 0.6

DEFAULT_CONFIG = {
    "format": "vertical",               # "vertical" = 1080x1920 | "carre" = 1080x1080
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "cut_mode": "energy",               # "energy" | "fixed"
    "cut_every": 2,                     # utilisé si cut_mode == "fixed"
    "energy_thresholds": (0.40, 0.75),  # percentiles bas / haut
    "energy_intervals": (4, 2, 1),      # beats par coupe : calme / moyen / intense
    "start": 0.0,
    "end": 30.0,
    "drop_time": None,                  # timestamp du drop dans le morceau (None = pas de drop connu)
    "buildup": 10.0,                    # s de buildup avant le drop dans la fenêtre auto
    # Passage ciblé : "drop" = moment fort (build-up + drop) | "calm" = passage calme.
    # NB : distinct du champ "section" des entrées d'EDL (buildup/drop) construit dans build_edl.
    "section": "drop",
    "strobe_beats": 16,                 # coupes forcées à 1 beat après le drop
    "effects": {"zoom": True, "flash": True, "shake": True, "speed": True,
                "blackout": False},     # strobe de build-up, opt-in
    "blackout_beats": 0.5,              # durée d'un éclair ET d'un noir, en beats
    "chrono": True,                     # extraits en ordre chronologique dans l'histoire du clip
    "min_presence": 0.3,                # score minimal « personnages à l'écran » d'une plage
    "accents": {"rgb": True, "glitch": True},  # RGB split à l'impact, micro-glitch temps forts
    "color_grade": "neutre",            # ambiance couleur : neutre|chaud|froid|delave
    "grain": 0.0,                       # texture film/VHS, 0.0–1.0
    "clip_speed": 1.0,                  # slow-mo global par segment, 0.5–1.5
    "speed_ramp": {                     # ramps calés sur les beats (interrupteur : effects.speed)
        "slow": 0.5,                    # segment d'anticipation (finit sur un impact), 0.5–1.0
        "fast": 1.4,                    # segment de relance (commence sur un impact), 1.0–1.5
        "impact_beats": 8,              # périodicité des impacts en beats ; 0 = pas de ramps
        "slow_beats": 2,                # beats fusionnés avant un impact ; 0 ou 1 = pas de fusion
        "min_dur": 0.25,                # s : en dessous (strobo), pas de ramp
        "interpolate": True,            # flux optique sur les segments ralentis
    },
    "end_scene": {                      # conclusion du montage : ralenti long figé
        "enabled": False,               # opt-in : aucun preset existant ne change
        "beats": 8,                     # durée totale de la scène, en beats
        "freeze": 1.0,                  # s de figé à la toute fin
        "speed": 0.5,
    },
    "subtitles": {                      # punchlines incrustées, générées par Claude
        "enabled": False,               # désactivé par défaut
        "mode": "llm",                  # "llm" = punchlines générées | "fixe" = texte écrit à la main
        "text": "",                     # mode fixe : caption unique, du début à la fin
        "x": 0.5,                       # ancrage horizontal, fraction de largeur (texte centré dessus)
        "y": 0.74,                      # ancrage vertical, fraction de hauteur
        "size": 64,                     # taille de police, px
        "preprompt": "",                # consigne de style (ex. « punchlines motivation gym »)
        "min_dur": 1.4,                 # durée min. d'affichage d'une punchline (lisibilité)
        "model": "claude-opus-4-8",     # modèle de génération
        "font": "impact",               # police embarquée : impact|classique|sobre|condensee|douce|elegante
    },
    "delogo": True,                     # gomme la zone du logo Crunchyroll (coin haut-gauche)
    "phrase_beats": 16,                 # fin de fenêtre calée sur des phrases de N beats
    "crf": 20,
    "preset": "medium",
    "audio_bitrate": "192k",
}


def merge_settings(base: dict, overrides: dict) -> dict:
    """Applique des réglages utilisateur sur une config, sans muter la base.
    Les clés inconnues sont ignorées ; les dicts imbriqués (effects, accents)
    sont fusionnés clé par clé."""
    merged: dict = {}
    for key, value in base.items():
        if key in overrides:
            if isinstance(value, dict) and isinstance(overrides[key], dict):
                merged[key] = {**value, **{k: v for k, v in overrides[key].items() if k in value}}
            else:
                merged[key] = overrides[key]
        else:
            merged[key] = dict(value) if isinstance(value, dict) else value
    return merged


# Formats de sortie. Le carré recadre beaucoup moins un rush 16:9 (44 % de la
# largeur jetée contre 68 % en vertical), ce qui sert notamment l'animé.
FORMATS = {"vertical": (1080, 1920), "carre": (1080, 1080)}


def apply_format(config: dict) -> dict:
    """Pose `width`/`height` d'après `format`, sans muter l'entrée. Un format
    inconnu retombe sur vertical — dégradation sûre, comme `section`."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in config.items()}
    out["width"], out["height"] = FORMATS.get(out.get("format", "vertical"),
                                              FORMATS["vertical"])
    return out


def _clamp_speed(value: float) -> float:
    """Borne moteur du slow-mo/accéléré. Défensif : l'UI n'impose pas de borne."""
    return max(0.5, min(1.5, float(value)))


def is_impact(beat_index: int, anchor: int, impact_beats: int) -> bool:
    """Un « impact » est un beat qui porte le motif de vitesse : l'ancre (le drop
    quand il existe) et tous les beats espacés d'un multiple d'`impact_beats`,
    avant comme après. `beat_index` négatif = borne de fenêtre, jamais un impact."""
    if beat_index < 0 or impact_beats <= 0:
        return False
    return (beat_index - anchor) % impact_beats == 0


def _ramp_decision(start_beat: int, end_beat: int, duration: float,
                    anchor: int, config: dict) -> tuple[float, bool]:
    """Calcule la vitesse ET si le ralenti vient de la règle de ramp (impact en
    fin de segment) — par opposition à `clip_speed`, un réglage global de preset
    qui peut lui aussi produire un ralenti. Cette distinction sert uniquement à
    décider du flux optique (`minterpolate`) au rendu : coûteux, il ne doit se
    déclencher que sur les ralentis voulus par la ramp, jamais sur clip_speed."""
    base = _clamp_speed(config.get("clip_speed", 1.0))
    ramp = config.get("speed_ramp") or {}
    if not config.get("effects", {}).get("speed"):
        return base, False
    if duration < float(ramp.get("min_dur", 0.25)):
        return base, False
    impact_beats = int(ramp.get("impact_beats", 8))
    if is_impact(end_beat, anchor, impact_beats):
        return _clamp_speed(ramp.get("slow", 0.5)), True
    if is_impact(start_beat, anchor, impact_beats):
        return _clamp_speed(ramp.get("fast", 1.4)), False
    return base, False


def ramp_speed(start_beat: int, end_beat: int, duration: float,
               anchor: int, config: dict) -> float:
    """Vitesse d'un segment : ralenti d'anticipation s'il FINIT sur un impact,
    accéléré de relance s'il COMMENCE sur un impact, sinon `clip_speed`. Quand les
    deux s'appliquent, le ralenti gagne (l'anticipation prime). Pure, sans RNG :
    la reproductibilité ne dépend pas d'elle."""
    return _ramp_decision(start_beat, end_beat, duration, anchor, config)[0]


def merge_boundaries_before_impacts(cut_beats: list[tuple[float, int]], anchor: int,
                                    config: dict) -> list[tuple[float, int]]:
    """Retire les coupes situées dans les `slow_beats` beats qui précèdent un
    impact : les segments concernés fusionnent en un seul, plus long, qui se
    termine sur l'impact et recevra le ralenti. Sans ça le ralenti subit la
    grille de coupe et dure un demi-beat après le drop — invisible.

    Le beat d'impact lui-même n'est jamais retiré (c'est lui qui porte le motif,
    et pour le drop c'est une coupe garantie). Pure, sans RNG."""
    ramp = config.get("speed_ramp") or {}
    slow_beats = int(ramp.get("slow_beats", 0))
    impact_beats = int(ramp.get("impact_beats", 8))
    if slow_beats < 1 or impact_beats <= 0 or not config.get("effects", {}).get("speed"):
        return cut_beats

    def distance_to_next_impact(beat_index: int) -> int:
        return (anchor - beat_index) % impact_beats  # 0 si le beat EST un impact

    # `slow_beats` est la LONGUEUR voulue du segment ralenti : pour qu'il couvre
    # N beats, il faut retirer les N-1 coupes intermédiaires, donc les distances
    # 1..N-1. À 1, rien n'est retiré — c'est la grille actuelle.
    kept = [(t, b) for t, b in cut_beats
            if not (0 < distance_to_next_impact(b) < slow_beats)]
    # Une fenêtre sans aucun impact verrait tout disparaître : on préfère la
    # grille d'origine à un montage d'un seul plan.
    return kept or cut_beats


def blackout_boundaries(boundaries: list[tuple[float, int]], drop_out: float,
                        beat_dur: float, config: dict,
                        fps: float) -> tuple[list[tuple[float, int]], set[int]]:
    """Remplace la grille du build-up par une alternance éclair / noir.

    Le comptage part **du drop et remonte** : c'est ce qui garantit que le
    segment se terminant sur le drop est un éclair d'image et non un noir —
    l'impact tombe donc sur une image. Compter depuis le début de la fenêtre
    laisserait la parité au hasard de la durée du build-up.

    Retourne les frontières réécrites et l'ensemble des **indices de frame** où
    commence un segment noir. Pure."""
    step = float(config.get("blackout_beats", 0.5)) * beat_dur
    frame = 1.0 / fps
    if step < frame or drop_out <= frame:
        return boundaries, set()          # rien à strober

    # Points de coupe à rebours depuis le drop, exclus.
    cuts: list[float] = []
    t = drop_out - step
    while t > frame - 1e-9:
        cuts.append(round(t * fps) / fps)
        t -= step
    cuts.reverse()
    # Doublons possibles après quantification si `step` est très court.
    cuts = [c for i, c in enumerate(cuts) if i == 0 or c - cuts[i - 1] >= frame - 1e-9]

    kept_after = [(t, b) for t, b in boundaries if t >= drop_out - 1e-9]
    rebuilt = [(0.0, -1)] + [(c, -1) for c in cuts if c >= frame - 1e-9] + kept_after

    # Un segment d'indice k compté à rebours depuis le drop (k=0 pour celui qui
    # s'y termine) est noir quand k est impair. Le segment de tête fait
    # exception : une vidéo qui s'ouvre sur du noir ressemble à un bug.
    starts_before = [t for t, _ in rebuilt if t < drop_out - 1e-9]
    black = {round(t * fps) for k, t in enumerate(reversed(starts_before))
             if k % 2 == 1 and k != len(starts_before) - 1}
    return rebuilt, black


SETTINGS_PATH = Path(__file__).parent / "settings.json"


def load_settings(path: Path | None = None) -> dict:
    """DEFAULT_CONFIG fusionné avec settings.json (réglages de l'interface web)."""
    config = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()}
    settings_path = path or SETTINGS_PATH
    if settings_path.is_file():
        config = merge_settings(config, json.loads(settings_path.read_text()))
    return config


def analyze_audio(track_path: Path) -> dict:
    """Grille de beats (s), BPM et enveloppe d'énergie RMS du morceau."""
    import librosa  # import paresseux : coûteux (~2 s), inutile pour la logique pure

    y, sr = librosa.load(str(track_path), sr=None, mono=True)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    rms = librosa.feature.rms(y=y)[0]
    return {
        "duration": float(librosa.get_duration(y=y, sr=sr)),
        "bpm": float(np.atleast_1d(tempo)[0]),
        "beats": np.asarray(beats, dtype=float),
        "energy": rms,
        "energy_times": librosa.times_like(rms, sr=sr),
    }


def load_clips(folder: Path) -> list[dict]:
    """Métadonnées des clips vidéo et images du dossier, triés par nom (déterminisme)."""
    clips = []
    for path in sorted(Path(folder).iterdir()):
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            kind = "image"
        elif suffix in VIDEO_EXTENSIONS:
            kind = "video"
        else:
            continue
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        info = json.loads(probe.stdout)
        stream = info["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        clips.append(
            {
                "path": path,
                "kind": kind,
                # Une image n'a pas de durée : ffprobe n'en donne pas et le
                # montage la boucle sur la durée du segment.
                "duration": None if kind == "image" else float(info["format"]["duration"]),
                "width": width,
                "height": height,
                "ratio": width / height,
            }
        )
    if not clips:
        raise ValueError(
            f"aucun clip ni image ({', '.join(sorted(VIDEO_EXTENSIONS | IMAGE_EXTENSIONS))}) "
            f"dans {folder}"
        )
    return clips


def classify_frames(frames: np.ndarray, sample_dt: float) -> dict:
    """Classe des frames échantillonnées (N, h, w, 3) uint8. Logique pure.

    - orange : dominante « carte Crunchyroll » (fond orange saturé)
    - black : frame quasi noire (générique, fondu)
    - motion : diff moyenne inter-frames, normalisée 0..1
    """
    f = frames.astype(np.int16)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    orange_pixels = (r > 150) & (g > 60) & (g < 180) & (b < 100) & (r > g) & (g > b)
    orange = orange_pixels.mean(axis=(1, 2)) > 0.35

    black = f.mean(axis=(1, 2, 3)) < 18.0

    motion = np.zeros(len(frames))
    if len(frames) > 1:
        motion[1:] = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2, 3)) / 255.0
        motion[0] = motion[1]
    return {"orange": orange, "black": black, "motion": motion}


# Détection des bandes noires (letterbox / pillarbox). Un extrait de film
# récupéré sur YouTube arrive souvent avec deux bandes : sans rognage, elles
# survivent au cadrage 9:16 et le ratio du conteneur fausse le choix du layout.
BAR_LUMA_MAX = 16.0        # une bande reste sous cette luminance (0-255)
BAR_MIN_FRACTION = 0.015   # en dessous, c'est du bruit de bord, pas une bande
BAR_MAX_TOTAL = 0.30       # au-delà, c'est une scène sombre : on ne rogne pas


def _edge_runs(profile: np.ndarray) -> tuple[int, int]:
    """Longueurs des segments SOMBRES CONTINUS en tête et en queue d'un profil.
    Une ligne sombre isolée au milieu n'est pas une bande et ne compte pas."""
    lit = np.flatnonzero(profile >= BAR_LUMA_MAX)
    if not len(lit):
        return len(profile), len(profile)   # tout est sombre : refusé en amont
    return int(lit[0]), int(len(profile) - 1 - lit[-1])


def content_rect(frames: np.ndarray) -> dict | None:
    """Rectangle utile d'un clip, en fractions du cadre, ou None si aucune bande.

    Le 95e percentile sur l'ensemble des frames — et non le maximum — évite
    qu'un sous-titre incrusté dans la bande ou un flash isolé masque la
    détection. Pure."""
    if not len(frames):
        return None
    luma = np.asarray(frames, dtype=np.float32).mean(axis=3)
    rows = np.percentile(luma.mean(axis=2), 95, axis=0)
    cols = np.percentile(luma.mean(axis=1), 95, axis=0)
    height, width = len(rows), len(cols)

    def keep(run: int, size: int) -> int:
        return run if run >= BAR_MIN_FRACTION * size else 0

    top, bottom = (keep(r, height) for r in _edge_runs(rows))
    left, right = (keep(c, width) for c in _edge_runs(cols))
    if top + bottom > BAR_MAX_TOTAL * height or left + right > BAR_MAX_TOTAL * width:
        return None
    if not (top or bottom or left or right):
        return None
    return {
        "x": left / width,
        "y": top / height,
        "w": (width - left - right) / width,
        "h": (height - top - bottom) / height,
    }


def usable_intervals(classification: dict, duration: float, sample_dt: float,
                     min_len: float = 1.0, margin: float = 0.5, motion_min: float = 0.008,
                     interval_motion_min: float = 0.05) -> list[dict]:
    """Plages temporelles exploitables d'un clip, avec leur mouvement moyen.

    Une plage = échantillons consécutifs ni orange, ni noirs, ni statiques
    (`motion_min` par échantillon), rognée de `margin` de chaque côté,
    longue d'au moins `min_len`. Les plages dont le mouvement MOYEN reste
    sous `interval_motion_min` (pans d'établissement, dialogues figés) sont
    écartées en bloc — le seuil par échantillon, lui, reste bas pour ne pas
    fragmenter les scènes d'action sur leurs micro-pauses.
    """
    ok = ~classification["orange"] & ~classification["black"] & (classification["motion"] >= motion_min)
    motion = classification["motion"]
    presence = classification.get("presence")  # score personnages par échantillon (optionnel)
    if presence is not None:
        ok = ok & (presence > 0.0)  # plan vide (ni visage ni contours) = inutilisable

    intervals: list[dict] = []
    run_start = None
    for i, good in enumerate([*ok, False]):  # sentinelle pour fermer la dernière run
        if good and run_start is None:
            run_start = i
        elif not good and run_start is not None:
            start = run_start * sample_dt + margin
            end = min(i * sample_dt, duration) - margin
            if end - start >= min_len and float(motion[run_start:i].mean()) >= interval_motion_min:
                intervals.append(
                    {
                        "start": start,
                        "end": end,
                        "motion": float(motion[run_start:i].mean()),
                        "presence": float(presence[run_start:i].mean()) if presence is not None else 1.0,
                    }
                )
            run_start = None
    return intervals


# Scène de fin : on ne cherche le climax que dans la queue du clip — en mode
# chrono, la fin de la timeline correspond à la fin de l'histoire.
FINAL_SCENE_TAIL = 1 / 3
# Poids du score : le duel prime (deux personnages face à face = l'affrontement),
# la présence et le mouvement départagent.
FINAL_SCENE_WEIGHTS = {"dual": 1.0, "presence": 0.6, "motion": 0.6}


def interval_dual_ratio(clip: dict, interval: dict) -> float:
    """Fraction d'échantillons « duel » (deux personnages face à face) sur une
    plage. `clip["dual"]` est un tableau par échantillon, pas un agrégat par
    plage : on le découpe sur scan_dt, comme le fait le cadrage. 0.0 si le clip
    n'a pas été scanné."""
    dual = clip.get("dual")
    if dual is None or not len(dual):
        return 0.0
    dt = clip.get("scan_dt") or (1.0 / SCAN_FPS)
    window = np.asarray(dual, dtype=bool)[int(interval["start"] / dt):
                                          math.ceil(interval["end"] / dt)]
    return float(window.mean()) if len(window) else 0.0


def find_final_scene(clips: list[dict], min_source: float = 0.0) -> dict | None:
    """Plage la plus « badass » de la queue des clips : le climax de l'histoire.
    Retourne {"clip", "interval"} ou None si rien d'exploitable — l'usine ne
    casse pas sur un catalogue non scanné, elle se termine normalement.

    Une plage qui déborde du dernier tiers n'est pas écartée : elle est
    restreinte à `[max(start, tail_start), end]`, sa portion utile. `motion`/
    `presence` restent la moyenne de la plage ENTIÈRE (pas de données par
    échantillon pour les recalculer) ; `interval_dual_ratio`, lui, est
    recalculé sur la portion restreinte — c'est gratuit et plus juste. Une
    plage entièrement avant `tail_start` reste écartée.

    `min_source` : durée de source nécessaire à la scène (dépend de
    `end_scene.beats`/`freeze`/`speed`, calculée par l'appelant) ; une plage
    dont la portion utile est plus courte n'est pas candidate — sinon le
    rendu lirait au-delà de la plage exploitable, dans des images que le
    scan a rejetées.

    Pure et sans RNG : à catalogue égal la scène est la même, donc la
    reproductibilité ne dépend pas du tirage seedé."""
    candidates: list[tuple[dict, dict, float, float]] = []
    for clip in clips:
        if clip.get("kind") == "image" or not clip.get("duration"):
            continue
        tail_start = clip["duration"] * (1.0 - FINAL_SCENE_TAIL)
        for interval in clip.get("intervals", []):
            if interval["end"] <= tail_start:
                continue
            start = max(interval["start"], tail_start)
            if interval["end"] - start < min_source:
                continue
            restricted = {**interval, "start": start}
            candidates.append((clip, restricted, interval_dual_ratio(clip, restricted),
                               float(restricted.get("motion", 0.0))))
    if not candidates:
        return None

    # Le mouvement n'est pas borné a priori : on le normalise sur les candidats
    # pour qu'un clip très agité n'écrase pas le critère de présence.
    max_motion = max(motion for _, _, _, motion in candidates) or 1.0
    weights = FINAL_SCENE_WEIGHTS

    def score(clip, interval, dual, motion) -> float:
        return (weights["dual"] * dual
                + weights["presence"] * float(interval.get("presence", 1.0))
                + weights["motion"] * (motion / max_motion))

    # Départage déterministe : meilleur score, puis nom de clip, puis plage la
    # plus tardive.
    best = min(candidates,
               key=lambda c: (-score(*c), str(c[0]["path"].name), -c[1]["start"]))
    return {"clip": best[0], "interval": best[1]}


SCAN_FPS = 2.0
# Version du cache de scan : incrémentée quand le format du payload change,
# pour que les entrées antérieures soient re-calculées et non lues à moitié.
SCAN_CACHE_VERSION = 2
SCAN_W, SCAN_H = 640, 360        # résolution de détection (visages + contours)
SMALL_W, SMALL_H = 32, 18        # résolution des heuristiques couleur/mouvement
CASCADE_PATH = Path(__file__).parent / "assets" / "lbpcascade_animeface.xml"
EDGE_PRESENCE_THRESHOLD = 0.008  # fraction de pixels « trait d'encre » dans la bande centrale


def _char_presence(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Par frame : score « personnages » (visage 1.0 / contours 0.6 / rien 0.0),
    centre d'intérêt horizontal (0..1) et détection de duel (visages aux deux bords).

    Un visage détecté N'IMPORTE OÙ compte : le recadrage intelligent ramènera le
    personnage dans le champ. Le coin du logo Crunchyroll est masqué avant
    détection. Les contours, eux, restent évalués dans la bande centrale."""
    import cv2  # import paresseux, comme librosa

    cascade = cv2.CascadeClassifier(str(CASCADE_PATH))
    if cascade.empty():
        raise RuntimeError(f"cascade animé introuvable ou invalide : {CASCADE_PATH}")
    n, height, width = frames.shape[:3]
    x0, x1 = int(width * 0.30), int(width * 0.70)
    presence = np.zeros(n)
    interest = np.full(n, 0.5)
    dual = np.zeros(n, dtype=bool)
    for i, frame in enumerate(frames):
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        gray[: int(height * 0.14), : int(width * 0.25)] = 0  # masque logo coin haut-gauche
        faces = cascade.detectMultiScale(
            cv2.equalizeHist(gray), scaleFactor=1.05, minNeighbors=2, minSize=(24, 24)
        )
        if len(faces):
            centers = np.array([x + w / 2 for x, y, w, h in faces])
            areas = np.array([w * h for x, y, w, h in faces], dtype=float)
            interest[i] = float((centers * areas).sum() / areas.sum()) / width
            if len(faces) >= 2:
                # Duel : détection STRICTE uniquement — le réglage permissif
                # voit des « visages » dans les textures de décor.
                strict = cascade.detectMultiScale(
                    cv2.equalizeHist(gray), scaleFactor=1.05, minNeighbors=5, minSize=(30, 30)
                )
                if len(strict) >= 2:
                    strict_x = np.array([x + w / 2 for x, y, w, h in strict])
                    dual[i] = strict_x.min() < 0.4 * width and strict_x.max() > 0.6 * width
            presence[i] = 1.0
            continue
        band = gray[:, x0:x1].astype(np.float32)
        magnitude = np.hypot(cv2.Sobel(band, cv2.CV_32F, 1, 0), cv2.Sobel(band, cv2.CV_32F, 0, 1))
        if float((magnitude > 160).mean()) > EDGE_PRESENCE_THRESHOLD:
            presence[i] = 0.6
            full = gray.astype(np.float32)
            mag_full = np.hypot(cv2.Sobel(full, cv2.CV_32F, 1, 0), cv2.Sobel(full, cv2.CV_32F, 0, 1))
            columns = (mag_full > 160).sum(axis=0)
            if columns.sum() > 0:
                interest[i] = float((np.arange(width) * columns).sum() / columns.sum()) / width
    return presence, interest, dual


def _scan_one(clip: dict) -> None:
    """Scan réel d'un clip (décodage FFmpeg + détections). Mute le dict."""
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(clip["path"]),
            "-vf", f"fps={SCAN_FPS},scale={SCAN_W}:{SCAN_H}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True, check=True,
    ).stdout
    frame_size = SCAN_W * SCAN_H * 3
    n = len(raw) // frame_size
    frames = np.frombuffer(raw[: n * frame_size], dtype=np.uint8).reshape(n, SCAN_H, SCAN_W, 3)
    # Couleur/mouvement sur miniatures 32x18 (moyenne par blocs de 20x20).
    small = frames.reshape(n, SMALL_H, SCAN_H // SMALL_H, SMALL_W, SCAN_W // SMALL_W, 3) \
                  .mean(axis=(2, 4)).astype(np.uint8)
    classification = classify_frames(small, 1.0 / SCAN_FPS)
    presence, interest_x, dual = _char_presence(frames)
    classification["presence"] = presence
    clip["intervals"] = usable_intervals(classification, clip["duration"], 1.0 / SCAN_FPS)
    clip["interest_x"] = interest_x
    clip["dual"] = dual
    clip["scan_dt"] = 1.0 / SCAN_FPS
    # Bandes noires : un extrait de film letterboxé doit être rogné avant tout
    # cadrage, et son ratio décrire le contenu, pas le conteneur.
    clip["crop"] = content_rect(frames)
    if clip["crop"] is not None:
        clip["ratio"] = ((clip["crop"]["w"] * clip["width"])
                         / (clip["crop"]["h"] * clip["height"]))


def _scan_payload(clip: dict) -> dict:
    return {
        "intervals": clip["intervals"],
        "interest_x": [float(x) for x in clip["interest_x"]],
        "dual": [bool(d) for d in clip["dual"]],
        "scan_dt": clip["scan_dt"],
        "crop": clip.get("crop"),
        "version": SCAN_CACHE_VERSION,
    }


def _apply_scan_payload(clip: dict, payload: dict) -> None:
    clip["intervals"] = payload["intervals"]
    clip["interest_x"] = np.array(payload["interest_x"], dtype=float)
    clip["dual"] = np.array(payload["dual"], dtype=bool)
    clip["scan_dt"] = payload["scan_dt"]
    clip["crop"] = payload.get("crop")
    if clip["crop"] is not None:
        clip["ratio"] = ((clip["crop"]["w"] * clip["width"])
                         / (clip["crop"]["h"] * clip["height"]))


def scan_clips(clips: list[dict], cache_dir: Path | None = None) -> list[dict]:
    """Enrichit chaque clip de ses plages exploitables : cartes orange, noir et
    passages statiques exclus, score de présence des personnages par plage.
    Avec cache par fichier (clé md5 du chemin, invalidé par mtime) quand
    cache_dir est fourni — on ne re-décode pas 30 clips à chaque génération."""
    for clip in clips:
        if clip.get("kind") == "image":
            continue  # rien à décoder ; sans clé `intervals` l'image est utilisable en entier
        cache_path = None
        if cache_dir is not None:
            digest = hashlib.md5(str(clip["path"]).encode()).hexdigest()
            cache_path = cache_dir / f"{digest}.json"
            if cache_path.is_file():
                # Cache tronqué/corrompu (process tué en pleine écriture) :
                # traité comme un miss, on re-scanne et on réécrit le cache.
                try:
                    cached = json.loads(cache_path.read_text())
                    if cached.get("version") == SCAN_CACHE_VERSION \
                            and cached.get("mtime") == clip["path"].stat().st_mtime:
                        _apply_scan_payload(clip, cached)
                        continue
                except (json.JSONDecodeError, OSError, KeyError):
                    pass
        _scan_one(clip)
        if cache_path is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(
                {"mtime": clip["path"].stat().st_mtime, **_scan_payload(clip)},
                ensure_ascii=False))
    return clips


def find_drop(analysis: dict, config: dict) -> float | None:
    """Timestamp du drop (calé sur un beat), ou None si l'énergie est trop plate.

    Le drop est l'instant qui maximise le contraste d'énergie entre les 8 s
    qui suivent et les 8 s qui précèdent (énergie lissée sur ~2 s).
    """
    dt = 0.25
    grid = np.arange(0.0, float(analysis["duration"]), dt)
    energy = np.interp(
        grid,
        np.asarray(analysis["energy_times"], dtype=float),
        np.asarray(analysis["energy"], dtype=float),
    )
    kernel = np.ones(max(1, int(2.0 / dt)))
    energy = np.convolve(energy, kernel / len(kernel), mode="same")

    window = int(8.0 / dt)
    if len(grid) < 2 * window + 1:
        return None
    csum = np.concatenate([[0.0], np.cumsum(energy)])
    idx = np.arange(window, len(energy) - window)
    contrast = (csum[idx + window] - csum[idx]) / window - (csum[idx] - csum[idx - window]) / window

    amplitude = float(energy.max() - energy.min())
    if amplitude <= 0.0 or float(contrast.max()) < 0.2 * amplitude:
        return None

    drop_time = grid[idx[int(np.argmax(contrast))]]
    beats = np.asarray(analysis["beats"], dtype=float)
    return float(beats[int(np.argmin(np.abs(beats - drop_time)))])


def find_calm(analysis: dict, config: dict, duration: float | str = 30.0) -> float | None:
    """Début (calé sur un beat) de la fenêtre la plus calme du morceau, ou None
    si le morceau est plus court que la fenêtre demandée.

    Miroir de `find_drop` : au lieu du contraste d'énergie maximal, on cherche la
    fenêtre de `duration` s à énergie moyenne minimale, en n'acceptant que les
    fenêtres SANS silence (leur minimum d'énergie reste au-dessus d'un seuil) —
    sinon on choisirait une intro/fade muet ou le bord du silence.

    Déterministe (aucun RNG) : ne casse pas la reproductibilité.
    NB : le "calm" ici concerne le CHOIX DU PASSAGE (config["section"]) ; à ne pas
    confondre avec le champ "section" (buildup/drop) des entrées d'EDL.
    """
    dt = 0.25
    grid = np.arange(0.0, float(analysis["duration"]), dt)
    energy = np.interp(
        grid,
        np.asarray(analysis["energy_times"], dtype=float),
        np.asarray(analysis["energy"], dtype=float),
    )
    kernel = np.ones(max(1, int(2.0 / dt)))
    energy = np.convolve(energy, kernel / len(kernel), mode="same")

    W = int(round(float(duration) / dt))
    if W < 1 or len(energy) < W:
        return None

    windows = np.lib.stride_tricks.sliding_window_view(energy, W)  # (N-W+1, W)
    means = windows.mean(axis=1)
    mins = windows.min(axis=1)

    silence = 0.05 * float(energy.max())
    musical = np.flatnonzero(mins >= silence)      # fenêtres sans silence
    if musical.size == 0:                          # morceau très faible partout
        musical = np.arange(len(means))
    best = int(musical[int(np.argmin(means[musical]))])

    beats = np.asarray(analysis["beats"], dtype=float)
    return float(beats[int(np.argmin(np.abs(beats - grid[best])))])


def snap_end_to_phrase(end: float, drop_time: float | None, beats: np.ndarray,
                       track_duration: float, phrase_beats: int = 16) -> float:
    """Étend la fin de fenêtre à la prochaine frontière de phrase (multiple de
    `phrase_beats` beats après le drop) pour que la musique s'arrête à un
    moment logique. Retombe sur la frontière précédente si ça dépasse le
    morceau ; inchangé sans drop connu."""
    beats = np.asarray(beats, dtype=float)
    if drop_time is None or len(beats) < 2:
        return end
    phrase = phrase_beats * float(np.median(np.diff(beats)))
    n = math.ceil((end - drop_time) / phrase - 1e-9)
    if drop_time + n * phrase > track_duration:
        n = math.floor((track_duration - drop_time) / phrase + 1e-9)
    return drop_time + n * phrase if n >= 1 else end


def resolve_window(analysis: dict, config: dict, start: float | None = None,
                   duration: float | str = 30.0) -> dict:
    """Résout drop_time / start / end dans config (et le retourne).

    config["section"] pilote le passage ciblé :
      - "drop" (défaut) : cadrage sur le drop détecté (buildup avant).
      - "calm" : cadrage sur la fenêtre la plus calme (find_calm), sans drop.
    start=None => cadrage auto ; start fourni (CLI --start) => prioritaire.
    duration="full" => tout le morceau ; sinon fin étendue à la frontière de phrase.
    """
    if config.get("section") == "calm":
        drop = None
        auto_start = (find_calm(analysis, config, duration)
                      if duration != "full" else None)
    else:
        drop = find_drop(analysis, config)
        auto_start = (max(0.0, drop - config["buildup"]) if drop is not None else 0.0)
    config["drop_time"] = drop
    if start is None:
        start = auto_start if auto_start is not None else 0.0
    config["start"] = float(start)
    if duration == "full":
        config["end"] = float(analysis["duration"])
    else:
        end = min(config["start"] + float(duration), float(analysis["duration"]))
        config["end"] = snap_end_to_phrase(
            end, drop, analysis["beats"], analysis["duration"], config["phrase_beats"]
        )
    return config


def frame_extract(clip: dict, clip_in: float, source_needed: float,
                  config: dict) -> tuple[float, str]:
    """Cadrage d'un extrait : centre d'intérêt horizontal et layout, moyennés
    sur la fenêtre consommée. Pure. Sans données de scan, on centre.

    Au moins 3 échantillons (1,5 s) : un extrait d'un beat n'en couvre parfois
    qu'un seul, trop peu pour juger dispersion et duel."""
    if "interest_x" not in clip:
        return 0.5, "crop"
    dt = clip.get("scan_dt", 1.0 / SCAN_FPS)
    i0 = int(clip_in / dt)
    i1 = max(i0 + 3, math.ceil((clip_in + source_needed) / dt))
    window_x = np.asarray(clip["interest_x"], dtype=float)[i0:i1]
    if not len(window_x):
        return 0.5, "crop"
    focus_x = float(np.clip(window_x.mean(), 0.0, 1.0))
    # `interest_x` est mesuré sur le cadre ENTIER, bandes comprises. Après
    # rognage, un clip à bandes latérales verrait son centre d'intérêt pointer
    # à côté : on remappe vers le contenu.
    crop = clip.get("crop")
    if crop and crop["w"] > 0:
        focus_x = float(np.clip((focus_x - crop["x"]) / crop["w"], 0.0, 1.0))
    # Les deux cadrages de secours dépendent du format de sortie. En 9:16 un
    # duel empilé fonctionne ; en 1:1 chaque moitié deviendrait une bande 2:1,
    # et le crop tient déjà les deux personnages.
    out_ratio = float(config["width"]) / float(config["height"])
    dual = np.asarray(clip.get("dual", []), dtype=bool)[i0:i1]
    if len(dual) and float(dual.mean()) >= 0.5 and out_ratio <= 0.75:
        return focus_x, "split"
    # Fond flouté seulement si la source est vraiment plus large que la sortie :
    # seuil 1,125 en vertical (tout 16:9 y a droit), 2,0 en carré (seul un scope).
    if float(window_x.std()) >= 0.18 and clip.get("ratio", 1.0) >= 2.0 * out_ratio:
        return focus_x, "blur"
    return focus_x, "crop"


def free_windows(intervals: list[dict], consumed: list[tuple[float, float]],
                 source_needed: float, margin: float = 0.5) -> list[dict]:
    """Portions des plages exploitables qui n'ont pas encore été montrées.

    Les portions consommées sont élargies de `margin` de chaque côté : sans
    cette marge, un nouvel extrait pourrait coller au précédent et rester
    visuellement identique — c'est précisément l'effet de répétition qu'on
    cherche à supprimer. Seules les fenêtres au moins aussi longues que
    `source_needed` sont retournées.

    Les dicts rendus ont la même forme que les plages d'entrée : `motion` et
    `presence` sont hérités du parent (ce sont déjà des moyennes, et on n'a pas
    les données par échantillon pour les recalculer sur une portion). Pure."""
    blocked = sorted((start - margin, end + margin) for start, end in consumed)
    windows: list[dict] = []
    for interval in intervals:
        cursor = interval["start"]
        for block_start, block_end in blocked:
            if block_end <= cursor or block_start >= interval["end"]:
                continue  # hors de cette plage
            if block_start - cursor >= source_needed:
                windows.append({**interval, "start": cursor, "end": block_start})
            cursor = max(cursor, block_end)
        if interval["end"] - cursor >= source_needed:
            windows.append({**interval, "start": cursor, "end": interval["end"]})
    return windows


def build_edl(analysis: dict, clips: list[dict], config: dict, seed: int) -> list[dict]:
    """Construit l'Edit Decision List. Logique pure : aucun I/O, déterministe à seed égal.

    Les timestamps de sortie sont quantifiés sur la grille de frames pour que
    l'erreur d'arrondi ne s'accumule pas d'un segment à l'autre (≤ ½ frame par cut).
    """
    fps = float(config["fps"])
    frame = 1.0 / fps
    start, end = float(config["start"]), float(config["end"])
    if end <= start:
        raise ValueError("fenêtre vide : end doit être > start")

    beats = np.asarray(analysis["beats"], dtype=float)
    rng = random.Random(seed)
    effects_cfg = config.get("effects", {})

    # Rang percentile d'énergie de chaque beat, calculé sur le morceau ENTIER
    # (pas la fenêtre) : 30 s de pur drop => coupes rapides partout. Sert au
    # rythme des coupes (mode energy) ET aux effets (tiers calme/moyen/intense).
    beat_energy = np.interp(
        beats,
        np.asarray(analysis["energy_times"], dtype=float),
        np.asarray(analysis["energy"], dtype=float),
    )
    ranks = beat_energy.argsort().argsort()
    percentiles = (ranks + 0.5) / max(1, len(beats))
    low_thr, high_thr = config["energy_thresholds"]
    calm_step, mid_step, intense_step = config["energy_intervals"]

    # Drop : uniquement s'il tombe dans la fenêtre, calé sur son beat.
    drop_idx = None
    drop_time = config.get("drop_time")
    if drop_time is not None and start <= drop_time < end:
        drop_idx = int(np.argmin(np.abs(beats - drop_time)))
        if not (start <= beats[drop_idx] < end):
            drop_idx = None
    strobe_beats = int(config.get("strobe_beats", 16))

    def step_at(i: int) -> int:
        if drop_idx is not None and drop_idx <= i < drop_idx + strobe_beats:
            return 1  # strobo au drop, quelle que soit l'énergie
        if config["cut_mode"] == "fixed":
            return max(1, int(config["cut_every"]))
        p = percentiles[i]
        return intense_step if p >= high_thr else mid_step if p >= low_thr else calm_step

    def tier_at(i: int) -> str:
        p = percentiles[i]
        return "intense" if p >= high_thr else "mid" if p >= low_thr else "calm"

    # --- Beats de coupe : marche beat par beat, sans jamais enjamber le drop ---
    cut_beats: list[tuple[float, int]] = []  # (timestamp piste, index du beat)
    in_window = np.flatnonzero((beats >= start) & (beats < end))
    if len(in_window):
        i, last = int(in_window[0]), int(in_window[-1])
        while i <= last:
            cut_beats.append((float(beats[i]), i))
            nxt = i + step_at(i)
            if drop_idx is not None and i < drop_idx < nxt:
                nxt = drop_idx  # garantit une coupe pile sur le drop
            i = nxt

    # Ancre des impacts : le drop quand il existe, sinon le premier beat de la
    # fenêtre (mode calme) — le motif de vitesse reste actif dans les deux cas.
    impact_anchor = drop_idx if drop_idx is not None else (
        int(in_window[0]) if len(in_window) else 0)

    # Les ralentis prennent leur temps : on retire les coupes qui morcelleraient
    # le segment d'anticipation. À faire AVANT la quantification, pour que
    # l'exemption `min_dur` juge la durée fusionnée et non celle d'origine.
    cut_beats = merge_boundaries_before_impacts(cut_beats, impact_anchor, config)

    # --- Frontières de segments : quantifiées frame, jamais < 1 frame d'écart ---
    out_end = round((end - start) * fps) / fps
    boundaries: list[tuple[float, int]] = [(0.0, -1)]  # -1 : début de fenêtre, pas un beat
    for t, beat_index in cut_beats:
        cut = round((t - start) * fps) / fps
        if cut - boundaries[-1][0] >= frame - 1e-9 and cut <= out_end - frame + 1e-9:
            boundaries.append((cut, beat_index))
    boundaries.append((out_end, -1))

    drop_out = None
    if drop_idx is not None:
        drop_out = round((float(beats[drop_idx]) - start) * fps) / fps

    # Strobe de build-up : la grille d'avant le drop devient une alternance
    # éclair / noir. Comptée à rebours depuis le drop, donc l'impact tombe
    # sur une image.
    black_frames: set = set()
    if effects_cfg.get("blackout") and drop_out is not None and len(beats) >= 2:
        boundaries, black_frames = blackout_boundaries(
            boundaries, drop_out, float(np.median(np.diff(beats))), config, fps)

    # Portions déjà montrées, par clip : le montage ne rejoue jamais un passage
    # (l'effet de retour en arrière que ça produisait cassait la fluidité).
    consumed: dict = {}

    # --- Scène de fin : un seul segment sur les N derniers beats -------------
    es_cfg = config.get("end_scene") or {}
    end_scene, es_start = None, None
    if es_cfg.get("enabled") and len(beats) >= 2:
        beat_dur = float(np.median(np.diff(beats)))
        raw_start = out_end - int(es_cfg.get("beats", 8)) * beat_dur
        candidate = round(max(0.0, raw_start) * fps) / fps
        # Une scène qui avalerait toute la fenêtre n'est plus une conclusion,
        # et une scène qui commencerait avant ou sur le drop lui volerait sa coupe.
        fits_window = candidate >= frame and candidate <= out_end - frame
        keeps_drop = not (drop_out is not None and candidate <= drop_out + frame)
        if fits_window and keeps_drop:
            # Durée de source nécessaire à CE candidat : sert de plancher à
            # find_final_scene pour qu'elle n'écarte pas les plages trop
            # courtes (le rendu lirait au-delà, dans du non-scanné).
            es_speed = _clamp_speed(es_cfg.get("speed", 0.5))
            scene_duration = out_end - candidate
            freeze = max(0.0, min(scene_duration, float(es_cfg.get("freeze", 1.0))))
            es_source = (scene_duration - freeze) * es_speed
            scene = find_final_scene(clips, min_source=es_source)
            if scene is not None:
                # Le climax est calculable dès maintenant : on le réserve pour
                # qu'aucun segment ordinaire ne le montre par avance.
                es_interval = scene["interval"]
                es_clip_in = max(es_interval["start"], es_interval["end"] - es_source)
                consumed.setdefault(scene["clip"]["path"], []).append(
                    (es_clip_in, es_clip_in + es_source))
                end_scene = {**scene, "clip_in": es_clip_in, "speed": es_speed,
                             "freeze": freeze, "source": es_source}
                es_start = candidate
                # On POSE la frontière : sans elle le segment final commencerait
                # à la dernière coupe existante, d'une durée arbitraire.
                boundaries = [b for b in boundaries if b[0] < es_start - 1e-9]
                boundaries.append((es_start, -1))
                boundaries.append((out_end, -1))

    def intervals_of(clip: dict) -> list[dict]:
        if "intervals" not in clip:  # pas scanné : clip entier utilisable
            return [{"start": 0.0, "end": clip["duration"], "motion": 1.0}]
        return clip["intervals"]  # scanné ([] = rien d'exploitable, clip exclu)

    # Vidéos et images vivent dans le même catalogue mais ne se montent pas
    # pareil : une image n'a pas de plage exploitable, elle n'a qu'un plafond
    # de durée et un écart minimum entre deux apparitions.
    video_clips = [c for c in clips if c.get("kind", "video") != "image"]
    image_clips = [c for c in clips if c.get("kind") == "image"]
    IMAGE_MIN_GAP = 3          # segments entre deux images (anti-diaporama)
    last_image_seg = -IMAGE_MIN_GAP

    # --- Attribution des clips : tirage seedé dans les plages exploitables ---
    edl: list[dict] = []
    prev_path = None
    drop_seg_count = 0
    for seg_index, ((seg_start, beat_index), (seg_end, end_beat)) in enumerate(
            zip(boundaries, boundaries[1:])):
        duration = seg_end - seg_start
        tier = tier_at(beat_index if beat_index >= 0 else (int(in_window[0]) if len(in_window) else 0))
        if drop_out is None:
            section = "main"
        else:
            section = "buildup" if seg_start < drop_out - 1e-9 else "drop"

        # Ramps : ralenti avant un impact, accéléré après. Le « gasp » historique
        # avant le drop en est un cas particulier — le drop est un impact.
        speed, ramp_slow = _ramp_decision(beat_index, end_beat, duration, impact_anchor, config)

        if round(seg_start * fps) in black_frames:
            # Écran noir : rien à montrer, donc rien à tirer et rien à
            # consommer au catalogue. FFmpeg génère la matière au rendu.
            edl.append(
                {
                    "timeline_start": seg_start,
                    "duration": duration,
                    "kind": "black",
                    "beat_index": beat_index,
                    "section": section,
                    "speed": 1.0,
                    "effects": [],
                }
            )
            continue

        if end_scene is not None and seg_start >= es_start - 1e-9:
            # Conclusion : ralenti long sur le climax, figé sur la dernière
            # image. Pas de tirage — la scène a été choisie hors du rng.
            clip = end_scene["clip"]
            es_speed = end_scene["speed"]
            freeze = end_scene["freeze"]
            es_source = end_scene["source"]
            clip_in = end_scene["clip_in"]
            focus_x, layout = frame_extract(clip, clip_in, es_source, config)
            entry_crop, clip_w, clip_h = None, clip["width"], clip["height"]
            rect = clip.get("crop")
            if rect is not None:
                clip_w = int(clip["width"] * rect["w"]) & ~1
                clip_h = int(clip["height"] * rect["h"]) & ~1
                entry_crop = {"x": int(clip["width"] * rect["x"]),
                              "y": int(clip["height"] * rect["y"]),
                              "w": clip_w, "h": clip_h}
            edl.append(
                {
                    "timeline_start": seg_start,
                    "duration": duration,
                    "clip_path": clip["path"],
                    "kind": "video",
                    "clip_in": clip_in,
                    "beat_index": beat_index,
                    "section": section,
                    "speed": es_speed,
                    "ramp_slow": True,   # ralenti voulu : il mérite le flux optique
                    "end_scene": True,
                    "freeze": freeze,
                    "effects": [],       # la scène se suffit ; pas de shake ni de glitch
                    "focus_x": focus_x,
                    "layout": layout,
                    "clip_w": clip_w,
                    "clip_h": clip_h,
                    "crop": entry_crop,
                }
            )
            continue

        effects: list[str] = []
        accents = config.get("accents", {})
        if effects_cfg.get("zoom") and (tier == "intense" or section == "drop"):
            effects.append("zoom")
        if section == "drop":
            if effects_cfg.get("flash") and drop_seg_count % 8 == 0:
                effects.append("flash")
                if accents.get("rgb"):
                    effects.append("rgb")  # aberration chromatique sur les impacts
            if effects_cfg.get("shake") and (
                drop_seg_count == 0 or (tier == "intense" and rng.random() < 0.3)
            ):
                effects.append("shake")
            if drop_seg_count > 0 and tier == "intense" \
                    and rng.random() < glitch_amount(accents):
                effects.append("glitch")
            drop_seg_count += 1
        elif effects_cfg.get("shake") and tier == "intense" and rng.random() < 0.3:
            effects.append("shake")

        source_needed = duration * speed
        chrono_mode = config.get("chrono", False)
        free = {}
        for c in video_clips:
            windows = free_windows(intervals_of(c), consumed.get(c["path"], []), source_needed)
            if chrono_mode:
                # Un clip qui a atteint son propre plafond (plus de plage libre
                # au-delà de ce qui a déjà servi) doit sortir du tirage ICI :
                # une fois choisi, Step 6 n'aurait d'autre choix que de revenir
                # en arrière dans son histoire, ce que le plancher interdit.
                floor = max((e for _, e in consumed.get(c["path"], [])), default=0.0)
                windows = [w for w in windows if w["end"] - source_needed >= floor - 1e-9]
            free[c["path"]] = windows
        usable = [c for c in video_clips if free[c["path"]]]
        if not usable:
            # Catalogue épuisé : plutôt que de faire échouer le lot, on rouvre
            # les plages entières. Mieux vaut un plan revu qu'une variante perdue.
            free = {c["path"]: [iv for iv in intervals_of(c)
                                if iv["end"] - iv["start"] >= source_needed]
                    for c in video_clips}
            usable = [c for c in video_clips if free[c["path"]]]
        if image_clips and duration <= IMAGE_MAX_DUR + 1e-9 \
                and seg_index - last_image_seg >= IMAGE_MIN_GAP:
            usable = usable + image_clips
        if not usable:
            raise ValueError(
                f"aucun clip n'a de plage exploitable de {source_needed:.2f}s "
                "(clips trop courts, trop de zones écartées par le scan, ou "
                "catalogue composé uniquement d'images — une image ne peut tenir "
                f"qu'un segment de {IMAGE_MAX_DUR:.2f}s au plus)"
            )
        pool = [c for c in usable if c["path"] != prev_path]
        if not pool:
            # Écarter le clip précédent viderait le pool : la coupure entre
            # deux plans du MÊME clip est ce que l'œil remarque le plus —
            # bien plus qu'un passage déjà montré revu ailleurs, plus tard.
            # On rouvre donc d'abord les plages entières des AUTRES clips
            # vidéo (repli différent de celui du catalogue globalement épuisé
            # ci-dessus, qui se déclenche plus tôt, sur un critère distinct).
            others = [c for c in video_clips if c["path"] != prev_path]
            for c in others:
                reopened = [iv for iv in intervals_of(c)
                            if iv["end"] - iv["start"] >= source_needed]
                if reopened:
                    free[c["path"]] = reopened
            pool = [c for c in others if free.get(c["path"])]
            pool += [c for c in usable if c.get("kind") == "image" and c["path"] != prev_path]
            if not pool:
                # Un seul clip vidéo exploitable dans tout le catalogue :
                # le repeat immédiat reste permis, faute d'alternative.
                pool = usable
        clip = rng.choice(pool)

        if clip.get("kind") == "image":
            # Flash court : pas de plage à choisir, pas de ralenti sur un fixe.
            # Le Ken Burns (sens tirés à la seed) évite l'image figée ; le zoom
            # ordinaire serait redondant avec lui.
            last_image_seg = seg_index
            prev_path = clip["path"]
            edl.append(
                {
                    "timeline_start": seg_start,
                    "duration": duration,
                    "clip_path": clip["path"],
                    "kind": "image",
                    "clip_in": 0.0,
                    "beat_index": beat_index,
                    "section": section,
                    "speed": 1.0,
                    "effects": [e for e in effects if e != "zoom"] + ["kenburns"],
                    "kenburns": {"zoom_dir": rng.choice([1, -1]),
                                 "pan_dir": rng.choice([1, -1])},
                    # Le scan n'a pas tourné : layout déduit du seul ratio.
                    "focus_x": 0.5,
                    # Même règle que les vidéos : le scan n'a pas tourné, mais le
                    # rapport source/sortie décide pareil.
                    "layout": ("blur"
                               if clip["ratio"] >= 2.0 * (config["width"] / config["height"])
                               else "crop"),
                    "clip_w": clip["width"],
                    "clip_h": clip["height"],
                }
            )
            continue

        candidates = free[clip["path"]]
        # Personnages à l'écran : écarte les plages quasi vides (fallback si toutes le sont).
        min_presence = config.get("min_presence", 0.0)
        candidates = [iv for iv in candidates if iv.get("presence", 1.0) >= min_presence] \
            or candidates

        if config.get("chrono", False):
            # Position dans la vidéo ≈ position dans l'histoire : le montage
            # avance dans le clip au rythme de la timeline (climax au drop).
            # Le point « voulu » se calcule sur la plage COMPLÈTE du clip
            # (stable), jamais sur les fenêtres libres : celles-ci rétrécissent
            # à chaque consommation, et faire porter la même fraction de
            # progression sur un total qui rapetisse fait s'emballer la
            # position vers la fin du clip bien avant la fin de la timeline.
            full = [iv for iv in intervals_of(clip)
                    if iv["end"] - iv["start"] >= source_needed] or intervals_of(clip)
            progress = seg_start / out_end if out_end > 0 else 0.0
            full_slacks = [iv["end"] - iv["start"] - source_needed for iv in full]
            target = progress * sum(full_slacks)
            desired_iv, desired_offset = full[-1], full_slacks[-1]
            for iv, slack in zip(full, full_slacks):
                if target <= slack:
                    desired_iv, desired_offset = iv, target
                    break
                target -= slack
            desired = desired_iv["start"] + desired_offset

            # Les fenêtres antérieures à ce qui a déjà servi sont écartées :
            # libres ou non, y revenir romprait la chronologie.
            floor = max((end for _, end in consumed.get(clip["path"], [])), default=0.0)
            ordered = [w for w in candidates
                       if w["end"] - source_needed >= floor - 1e-9] or candidates
            # Fenêtre libre la plus proche de la position voulue.
            window = min(
                ordered,
                key=lambda w: abs(min(max(desired, w["start"]), w["end"] - source_needed) - desired),
            )
            clip_in = min(max(desired, window["start"]), window["end"] - source_needed) \
                + rng.uniform(0.0, 1.0)
            clip_in = min(max(clip_in, window["start"]), window["end"] - source_needed)
        else:
            if (section == "drop" or tier == "intense") and len(candidates) > 1:
                # Les moments intenses piochent dans les plages les plus nerveuses.
                median_motion = float(np.median([iv["motion"] for iv in candidates]))
                candidates = [iv for iv in candidates if iv["motion"] >= median_motion] \
                    or candidates
            window = rng.choice(candidates)
            clip_in = rng.uniform(window["start"], window["end"] - source_needed)
        prev_path = clip["path"]

        # Cadrage : centre d'intérêt et layout, moyennés sur l'extrait choisi.
        focus_x, layout = frame_extract(clip, clip_in, source_needed, config)

        # Rectangle utile en pixels. Les dimensions passées à l'entrée sont
        # celles du CONTENU : le delogo, exprimé en fractions de clip_w/clip_h,
        # se recale ainsi tout seul sur le vrai coin de l'image.
        entry_crop, clip_w, clip_h = None, clip["width"], clip["height"]
        rect = clip.get("crop")
        if rect is not None:
            clip_w = int(clip["width"] * rect["w"]) & ~1   # dimensions paires
            clip_h = int(clip["height"] * rect["h"]) & ~1
            entry_crop = {"x": int(clip["width"] * rect["x"]),
                          "y": int(clip["height"] * rect["y"]),
                          "w": clip_w, "h": clip_h}

        edl.append(
            {
                "timeline_start": seg_start,
                "duration": duration,
                "clip_path": clip["path"],
                "kind": "video",
                "clip_in": clip_in,
                "beat_index": beat_index,
                "section": section,
                "speed": speed,
                "ramp_slow": ramp_slow,
                "effects": effects,
                "focus_x": focus_x,
                "layout": layout,
                "clip_w": clip_w,
                "clip_h": clip_h,
                "crop": entry_crop,
            }
        )
        consumed.setdefault(clip["path"], []).append((clip_in, clip_in + source_needed))
    return edl


# --- Punchlines incrustées (sous-titres générés) ----------------------------

_CAPTION_FONTS = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",   # look motivation/edit
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _caption_font() -> str | None:
    for path in _CAPTION_FONTS:
        if Path(path).is_file():
            return path
    return None


FONTS_DIR = Path(__file__).parent / "assets" / "fonts"

_FONT_FILES = {  # nom logique -> fichier embarqué (licences OFL)
    "impact": "Anton-Regular.ttf",
    "classique": "Montserrat-ExtraBold.ttf",
    "sobre": "OpenSans-Bold.ttf",
    "condensee": "BebasNeue-Regular.ttf",
    "douce": "Baloo2-Bold.ttf",
    "elegante": "CormorantGaramond-SemiBold.ttf",
}


def resolve_caption_font(name: str) -> str | None:
    """Chemin de la police d'un nom logique ; nom inconnu = impact ;
    fichier absent = repli sur les polices système (_caption_font)."""
    path = FONTS_DIR / _FONT_FILES.get(name, _FONT_FILES["impact"])
    if path.is_file():
        return str(path)
    return _caption_font()


def _drawtext_escape(text: str) -> str:
    """Échappe le texte pour l'option drawtext de FFmpeg (argument non shell)."""
    out = text.replace("\\", "\\\\")
    for ch in (":", "'", "%", ",", ";", "[", "]"):
        out = out.replace(ch, "\\" + ch)
    # Un retour à la ligne réel casse le parseur de filtergraph ; drawtext
    # interprète la séquence \n comme un saut de ligne. Fait en dernier : le
    # doublement des antislashs ci-dessus ne doit pas s'y appliquer.
    out = out.replace("\r\n", "\n").replace("\n", "\\n")
    return out


def _drawtext_fontfile(path: str) -> str:
    """Chemin de police échappé pour le filtergraph FFmpeg. Sous Windows, les
    antislashs et le deux-points du lecteur (`C:\\...`) cassent le parseur, qui
    applique DEUX niveaux d'unescape : le `:` doit donc être précédé de **deux**
    antislashs (`C\\\\:/...`), un seul ne suffit pas. On passe aussi en slashs.
    No-op sur les chemins POSIX (ni `\\` ni `:`)."""
    return path.replace("\\", "/").replace(":", "\\\\:")


def assign_caption_slots(edl: list[dict], min_dur: float) -> int:
    """Regroupe les segments en créneaux de sous-titre : une punchline reste
    affichée ≥ `min_dur` (lisibilité) puis change à la coupe suivante. Annote
    chaque entrée d'un `caption_slot` (index) ; retourne le nombre de créneaux."""
    slot = -1
    slot_start = 0.0
    for entry in edl:
        if slot < 0 or entry["timeline_start"] - slot_start >= min_dur - 1e-9:
            slot += 1
            slot_start = entry["timeline_start"]
        entry["caption_slot"] = slot
    return slot + 1


def _load_dotenv(path: Path | None = None) -> None:
    """Charge .env dans os.environ (sans écraser l'existant) pour que
    ANTHROPIC_API_KEY posée dans .env soit prise en compte sans export manuel."""
    env_path = path or (Path(__file__).parent / ".env")
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# Consigne partagée par tous les backends LLM.
_PUNCHLINE_SYSTEM = (
    "Tu écris des punchlines courtes et percutantes incrustées sur des edits "
    "vidéo verticaux. Chaque punchline fait 2 à 6 mots, sans hashtag, sans emoji, "
    "sans ponctuation finale, et forme une progression cohérente d'une à l'autre.")


def _punchline_user_prompt(preprompt: str, count: int, seed: int) -> str:
    return (f"Génère exactement {count} punchlines distinctes.\n"
            f"Style / consigne : {preprompt}\nVariation n°{seed}.")


def _llm_backend() -> str:
    """Backend LLM courant : LM Studio (local, coût nul) par défaut."""
    return os.environ.get("LLM_BACKEND", "lmstudio").strip().lower()


def _call_anthropic(preprompt: str, count: int, seed: int, model: str) -> list[str]:
    """Génère `count` punchlines via l'API Anthropic (Claude). Sortie JSON structurée."""
    import anthropic

    _load_dotenv()
    client = anthropic.Anthropic()
    schema = {
        "type": "object",
        "properties": {"punchlines": {"type": "array", "items": {"type": "string"}}},
        "required": ["punchlines"],
        "additionalProperties": False,
    }
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_PUNCHLINE_SYSTEM,
        messages=[{"role": "user", "content": _punchline_user_prompt(preprompt, count, seed)}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return [str(p) for p in json.loads(text)["punchlines"]]


def _call_lmstudio(preprompt: str, count: int, seed: int, model: str) -> list[str]:
    """Génère `count` punchlines via un serveur local compatible OpenAI (LM Studio) →
    coût nul. Endpoint et modèle configurables par LMSTUDIO_BASE_URL / LMSTUDIO_MODEL.
    `seed` est transmis au serveur pour la reproductibilité. Le `model` Claude n'est
    pas pertinent ici (le serveur utilise le modèle chargé)."""
    import urllib.request

    _load_dotenv()
    base = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1").rstrip("/")
    lm_model = os.environ.get("LMSTUDIO_MODEL", "local-model")
    body = {
        "model": lm_model,
        "messages": [
            {"role": "system", "content": _PUNCHLINE_SYSTEM
             + ' Réponds UNIQUEMENT en JSON : {"punchlines": ["...", "..."]}.'},
            {"role": "user", "content": _punchline_user_prompt(preprompt, count, seed)},
        ],
        "temperature": 0.8,
        "seed": seed,
        # LM Studio >= 0.4 exige json_schema (l'ancien json_object renvoie 400).
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "punchlines",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "punchlines": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["punchlines"],
                },
            },
        },
    }
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    text = data["choices"][0]["message"]["content"]
    return [str(p) for p in json.loads(text)["punchlines"]]


# Nom de fonction par backend (résolu via globals() au moment de l'appel pour
# rester monkeypatchable dans les tests).
_LLM_BACKENDS = {"anthropic": "_call_anthropic", "lmstudio": "_call_lmstudio"}


def _call_llm(preprompt: str, count: int, seed: int, model: str) -> list[str]:
    """Génère `count` punchlines via le backend choisi par LLM_BACKEND (défaut
    `lmstudio` = local, coût nul). Si le primaire échoue et que LLM_FALLBACK nomme
    un autre backend (ex. `anthropic`), il est essayé en repli. Isolé pour être
    mocké dans les tests."""
    primary = _llm_backend()
    order = [primary]
    fallback = os.environ.get("LLM_FALLBACK", "").strip().lower()
    if fallback and fallback != primary:
        order.append(fallback)
    last_exc: Exception | None = None
    for name in order:
        fnname = _LLM_BACKENDS.get(name)
        if fnname is None:
            continue
        try:
            return globals()[fnname](preprompt, count, seed, model)
        except Exception as exc:  # on tente le repli, sinon on remonte l'erreur
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"backend LLM inconnu : {primary!r}")


def generate_punchlines(preprompt: str, count: int, seed: int,
                        cache_dir: Path | None = None,
                        model: str = "claude-opus-4-8") -> list[str]:
    """Punchlines pour une vidéo. Mises en cache par (modèle, préprompt, count,
    seed) → reproductibles à seed égal. Dégrade en [] si pas de clé / échec API
    (l'usine ne bloque jamais sur le LLM)."""
    if count <= 0 or not preprompt.strip():
        return []
    cache_path = None
    if cache_dir is not None:
        key = hashlib.md5(
            f"{_llm_backend()}|{model}|{preprompt}|{count}|{seed}".encode()).hexdigest()
        cache_path = cache_dir / f"{key}.json"
        if cache_path.is_file():
            try:
                return json.loads(cache_path.read_text())["punchlines"][:count]
            except (json.JSONDecodeError, OSError, KeyError):
                pass
    try:
        punchlines = _call_llm(preprompt, count, seed, model)[:count]
    except Exception:
        return []
    if cache_path is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"punchlines": punchlines}, ensure_ascii=False))
    return punchlines


def apply_subtitles(edl: list[dict], config: dict, seed: int,
                    cache_dir: Path | None = None) -> list[dict]:
    """Annote l'EDL de punchlines (clé `caption` par segment) si les sous-titres
    sont activés. Segments d'un même créneau partagent la punchline ; texte vide
    si la génération échoue (rendu sans sous-titres, jamais de plantage)."""
    sub = config.get("subtitles") or {}
    if not sub.get("enabled"):
        return edl
    if sub.get("mode") == "fixe":
        # Caption unique écrite à la main : ni créneaux, ni LLM, ni cache.
        text = sub.get("text", "")
        for entry in edl:
            entry["caption"] = text
        return edl
    n = assign_caption_slots(edl, float(sub.get("min_dur", 1.4)))
    lines = generate_punchlines(sub.get("preprompt", ""), n, seed, cache_dir,
                                sub.get("model", "claude-opus-4-8"))
    for entry in edl:
        i = entry.get("caption_slot", 0)
        entry["caption"] = lines[i] if i < len(lines) else ""
    return edl


def generate_video(track_path, clips: list[dict], config: dict, seed: int,
                   output_path, *, start: float | None = None,
                   duration: float | str = 30, subtitles_cache_dir: Path | None = None,
                   log=lambda m: None) -> dict:
    """Produit UNE vidéo montée à partir d'un morceau + clips pré-scannés.
    Point d'entrée réutilisable (CLI et usine par niche). `config` n'est pas
    muté (copie interne). Retourne un récapitulatif {segments, window, captions}."""
    analysis = analyze_audio(Path(track_path))
    cfg = apply_format(config)   # copie interne + dimensions dérivées du format
    resolve_window(analysis, cfg, start=start, duration=duration)
    drop = cfg["drop_time"]
    log(f"  {analysis['bpm']:.0f} BPM ; fenêtre {cfg['start']:.1f}→{cfg['end']:.1f}s"
        + (f" (drop {drop:.1f}s)" if drop is not None else " (pas de drop net)"))

    edl = build_edl(analysis, clips, cfg, seed=seed)
    log(f"  EDL : {len(edl)} segments (seed {seed})")

    captions = []
    if (cfg.get("subtitles") or {}).get("enabled"):
        _llm_names = {"lmstudio": "LM Studio local", "anthropic": "Claude"}
        _backend = _llm_backend()
        log(f"  génération des punchlines ({_llm_names.get(_backend, _backend)})…")
        apply_subtitles(edl, cfg, seed=seed, cache_dir=subtitles_cache_dir)
        captions = sorted({e["caption"] for e in edl if e.get("caption")})
        log(f"  {len(captions)} punchline(s)" if captions
            else "  aucune punchline (LLM indisponible ? rendu sans texte)")

    render(edl, Path(track_path), Path(output_path), cfg)
    return {"segments": len(edl), "window": (cfg["start"], cfg["end"]), "captions": captions}


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg a échoué :\n  ffmpeg {' '.join(args)}\n{result.stderr}")


def color_grade_filter(grade: str) -> str:
    """Fragment FFmpeg d'étalonnage couleur pour un segment. '' si neutre/inconnu."""
    return {
        "chaud": "eq=gamma_r=1.06:gamma_b=0.94:saturation=1.05",
        "froid": "eq=gamma_b=1.06:gamma_r=0.94:saturation=0.98",
        "delave": "eq=saturation=0.72:contrast=0.94:brightness=0.03",
    }.get(grade, "")


def grain_filter(amount: float) -> str:
    """Fragment FFmpeg de grain/VHS pour un segment. '' si amount <= 0.
    Bruit temporel proportionnel ; dérive chroma permanente au-delà de 0.6 (VHS).
    Défensif : clampe l'entrée à [0.0, 1.0] (l'UI n'impose pas de borne)."""
    amount = max(0.0, min(1.0, amount))
    if amount <= 0:
        return ""
    frag = f"noise=alls={round(amount * 24)}:allf=t"
    if amount >= 0.6:
        frag += ",rgbashift=rh=2:bh=-2"
    return frag


def glitch_amount(accents: dict) -> float:
    """Intensité de glitch 0.0–1.0 depuis accents['glitch'].
    Compat : bool True→0.6, False/absent→0.0 ; nombre clampé."""
    value = accents.get("glitch", False)
    if isinstance(value, bool):
        return 0.6 if value else 0.0
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _segment_input_args(entry: dict) -> list[str]:
    """Arguments d'entrée FFmpeg d'un segment. Une image est bouclée (`-loop 1`)
    et n'a pas de point d'entrée ; une vidéo est seekée AVANT `-i` (seek rapide).
    Le rab de 0,5 s absorbe l'imprécision du seek : `-frames:v` coupe pile.

    Un segment à figé (`freeze > 0`) veut au contraire manquer de source par
    construction — c'est ce que `tpad=stop_mode=clone` comble. Lui laisser le
    rab de seek reviendrait à amputer le figé (le rab, étiré par `1/speed`,
    peut annuler tout le budget de figé) : on ne l'applique donc qu'aux
    segments ordinaires. Un seek légèrement imprécis sur un segment à figé
    ne fait qu'allonger le figé de quelques frames clonées en plus —
    imperceptible."""
    # Le figé de fin ne consomme pas de source : `tpad=stop_mode=clone` clone la
    # dernière image et `-frames:v` garde le compte exact. Pas de filtre dédié.
    freeze = float(entry.get("freeze", 0.0))
    source_needed = max(0.0, entry["duration"] - freeze) * entry.get("speed", 1.0)
    margin = 0.0 if freeze > 0.0 else 0.5
    path = str(entry["clip_path"])
    if entry.get("kind") == "image":
        return ["-loop", "1", "-t", f"{source_needed + margin:.6f}", "-i", path]
    return ["-ss", f"{entry['clip_in']:.6f}", "-t", f"{source_needed + margin:.6f}",
            "-i", path]


def kenburns_filter(entry: dict, config: dict) -> str:
    """Zoom + pan lents sur une image fixe, pour qu'elle ne soit jamais figée.
    Les sens sont tirés à la seed dans build_edl : le filtre est déterministe."""
    width, height, fps = config["width"], config["height"], config["fps"]
    kb = entry.get("kenburns") or {}
    n = max(1, round(entry["duration"] * fps))
    z = f"1.02+0.10*on/{n}" if kb.get("zoom_dir", 1) > 0 else f"1.12-0.10*on/{n}"
    pan = 1 if kb.get("pan_dir", 1) > 0 else -1
    x = f"iw/2-(iw/zoom/2)+{pan}*(on/{n})*iw*0.04"
    return (f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={width}x{height}:fps={fps}")


def _segment_filters(entry: dict, config: dict) -> list[str]:
    """Arguments FFmpeg de filtrage d'un segment : ["-vf", ...] pour un cadrage
    simple, ["-filter_complex", ..., "-map", "[v]"] pour split-screen et fond
    flouté. Ordre : slow-mo → layout → fps → punch-zoom → flash → RGB/glitch →
    normalisation → tpad (complété par -frames:v)."""
    width, height, fps = config["width"], config["height"], config["fps"]
    effects = entry.get("effects", [])
    layout = entry.get("layout", "crop")
    focus_x = entry.get("focus_x", 0.5)
    speed = entry.get("speed", 1.0)

    pre = ""
    crop = entry.get("crop")
    if crop:
        # Bandes noires retirées AVANT tout le reste : sans ça elles survivent
        # au cadrage 9:16 et se retrouvent dans la vidéo finale.
        pre += f"crop={crop['w']}:{crop['h']}:{crop['x']}:{crop['y']},"
    if config.get("delogo") and "clip_w" in entry and entry.get("kind") != "image":
        # Gomme le logo de chaîne (coin haut-gauche) AVANT recadrage : le
        # recadrage intelligent ou le fond flouté peuvent le faire entrer au champ.
        # Réservé aux rushes vidéo : une image fixe (affiche, visuel uploadé)
        # n'a pas de logo de chaîne à gommer, et le rectangle flouté l'abîmerait
        # pour rien.
        cw, ch = entry["clip_w"], entry["clip_h"]
        pre += (f"delogo=x={max(1, int(cw * 0.01))}:y={max(1, int(ch * 0.02))}"
                f":w={int(cw * 0.22)}:h={int(ch * 0.10)},")
    if speed != 1.0:
        pre += f"setpts=(PTS-STARTPTS)/{speed:.6f},"

    # --- Chaîne commune post-layout (opère sur du 1080x1920) ---
    # Flux optique sur les ralentis DE RAMP : minterpolate invente les images
    # manquantes entre les images réelles. Placé ici — donc APRÈS le setpts (dans
    # `pre`) et APRÈS le scale/crop — il travaille sur du 1080x1920 déjà cadré
    # plutôt que sur la source, et sert les trois layouts sans duplication.
    # Coûteux (5 à 15x le temps d'encodage du segment) : on le déclenche
    # uniquement quand `build_edl` a marqué le segment `ramp_slow` (ralenti
    # voulu par la règle de ramp), jamais sur un ralenti venant de `clip_speed`,
    # un réglage global de preset qui n'a rien à voir avec la ramp — sinon un
    # preset à clip_speed=0.85 rend TOUS les segments coûteux à interpoler.
    if entry.get("ramp_slow") and (config.get("speed_ramp") or {}).get("interpolate"):
        post = [f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"]
    else:
        post = [f"fps={fps}"]
    if "kenburns" in effects:
        post.append(kenburns_filter(entry, config))
    if "zoom" in effects:
        post.append(
            "zoompan=z='1+0.10*max(0,1-on/6)'"
            f":x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d=1:s={width}x{height}:fps={fps}"
        )
    if "flash" in effects:
        post.append("fade=t=in:st=0:d=0.1:color=white")
    if "glitch" in effects:
        post.append("rgbashift=rh=-14:gv=10:bh=14:edge=smear:enable='lt(n,2)'")
    elif "rgb" in effects:
        post.append("rgbashift=rh=8:bh=-8:edge=smear:enable='lt(n,3)'")
    grade = color_grade_filter(config.get("color_grade", "neutre"))
    if grade:
        post.append(grade)
    grain = grain_filter(config.get("grain", 0.0))
    if grain:
        post.append(grain)
    # Punchline incrustée (après les accents pour rester nette). Position et
    # taille réglables : le texte est centré sur le point d'ancrage (x, y),
    # exprimé en fraction d'écran. Vaut pour les deux modes, généré et fixe.
    cap = entry.get("caption")
    subs = config.get("subtitles", {})
    font = resolve_caption_font(subs.get("font", "impact"))
    if cap and font:
        # Dernière ligne de défense avant le rendu : une valeur absente, `None`
        # ou non convertible (ex. formulaire vidé, JSON malformé) retombe sur
        # le défaut au lieu de faire planter le rendu — même principe que
        # generate_punchlines qui dégrade en `[]` plutôt que de bloquer l'usine.
        def _coerce(value, cast, default):
            try:
                return cast(value)
            except (TypeError, ValueError):
                return default

        cap_x = max(0.0, min(1.0, _coerce(subs.get("x", 0.5), float, 0.5)))
        cap_y = max(0.0, min(1.0, _coerce(subs.get("y", 0.74), float, 0.74)))
        cap_size = max(8, _coerce(subs.get("size", 64), int, 64))
        post.append(
            f"drawtext=fontfile={_drawtext_fontfile(font)}:text={_drawtext_escape(cap)}"
            f":fontsize={cap_size}:fontcolor=white:borderw=5:bordercolor=black@0.9"
            f":x=w*{cap_x:.4f}-text_w/2:y=h*{cap_y:.4f}-text_h/2"
        )
    post.append("setsar=1,format=yuv420p")
    # 1 s de marge pour l'imprécision de seek, plus la durée du figé de fin.
    freeze = float(entry.get("freeze", 0.0))
    post.append(f"tpad=stop_mode=clone:stop_duration={1 + freeze:g}")
    post_chain = ",".join(post)

    if layout == "split":
        # Duel : moitiés gauche/droite empilées haut/bas (1080x960 chacune).
        half_h = height // 2
        graph = (
            f"[0:v]{pre}split=2[l0][r0];"
            f"[l0]crop=iw/2:ih:0:0,scale={width}:{half_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{half_h}[l1];"
            f"[r0]crop=iw/2:ih:iw/2:0,scale={width}:{half_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{half_h}[r1];"
            f"[l1][r1]vstack=inputs=2,{post_chain}[v]"
        )
        return ["-filter_complex", graph, "-map", "[v]"]

    if layout == "blur":
        # Plan entier visible, centré sur fond flouté-assombri.
        graph = (
            f"[0:v]{pre}split=2[bg0][fg0];"
            f"[bg0]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=luma_radius=24:luma_power=2,"
            "eq=brightness=-0.06[bg1];"
            f"[fg0]scale={width}:-2[fg1];"
            f"[bg1][fg1]overlay=(W-w)/2:(H-h)/2,{post_chain}[v]"
        )
        return ["-filter_complex", graph, "-map", "[v]"]

    # Layout crop : la fenêtre 9:16 se cale sur le centre d'intérêt détecté.
    pad_w, pad_h = (20, 38) if "shake" in effects else (0, 0)
    x_expr = f"min(max(iw*{focus_x:.4f}-{width / 2:.0f},0),iw-{width})"
    y_expr = f"(ih-{height})/2"
    if "shake" in effects:
        x_expr = f"min(max(iw*{focus_x:.4f}-{width / 2:.0f}+7*sin(n*7.3),0),iw-{width})"
        y_expr = f"min(max((ih-{height})/2+7*cos(n*9.1),0),ih-{height})"
    vf = (
        f"{pre}scale={width + pad_w}:{height + pad_h}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:x='{x_expr}':y='{y_expr}',{post_chain}"
    )
    return ["-vf", vf]


def render(edl: list[dict], audio_path: Path, output_path: Path, config: dict) -> None:
    """Assemble la vidéo : un segment ré-encodé par entrée d'EDL, puis concat
    en copie de flux + piste audio du morceau."""
    fps = config["fps"]
    total = edl[-1]["timeline_start"] + edl[-1]["duration"]

    with tempfile.TemporaryDirectory(prefix="beatsync-") as tmp:
        tmpdir = Path(tmp)
        concat_list = tmpdir / "segments.txt"
        lines = []
        for i, entry in enumerate(edl):
            segment = tmpdir / f"seg{i:04d}.mp4"
            # Nombre de frames EXACT : les sources à fps non multiples (23,976…)
            # peuvent rendre quelques ms de moins que demandé, et le concat
            # accumulerait la dérive. tpad clone la dernière frame au besoin,
            # -frames:v coupe pile au bon compte.
            n_frames = round(entry["duration"] * fps)
            _run_ffmpeg(
                [
                    *_segment_input_args(entry),
                    *_segment_filters(entry, config),
                    "-frames:v", str(n_frames),
                    "-an",
                    "-c:v", "libx264", "-preset", config["preset"], "-crf", str(config["crf"]),
                    # Timescale commun : requis pour le concat en copie de flux
                    "-video_track_timescale", "15360",
                    "-bitexact", "-map_metadata", "-1",
                    str(segment),
                ]
            )
            lines.append(f"file '{segment}'")
        concat_list.write_text("\n".join(lines) + "\n")

        _run_ffmpeg(
            [
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-ss", f"{config['start']:.6f}", "-t", f"{total:.6f}", "-i", str(audio_path),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", config["audio_bitrate"],
                "-bitexact", "-map_metadata", "-1",
                "-shortest",
                str(output_path),
            ]
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Montage vidéo 9:16 ou 1:1 synchronisé sur les beats d'un morceau."
    )
    parser.add_argument("track", help="chemin du morceau audio")
    parser.add_argument("clips_dir", help="dossier des clips vidéo")
    parser.add_argument("-o", "--output", default="output.mp4", help="fichier de sortie (défaut : output.mp4)")
    parser.add_argument("--seed", type=int, default=42, help="graine de reproductibilité (défaut : 42)")
    parser.add_argument("--start", type=float, default=None,
                        help="début manuel de la fenêtre, en s (défaut : cadrage auto buildup + drop)")
    parser.add_argument("--duration", default="30", help='durée de la fenêtre en s, ou "full" (défaut : 30)')
    parser.add_argument("--section", choices=["drop", "calm"], default=None,
                        help='passage ciblé : "drop" (moment fort, défaut) ou "calm" (passage calme)')
    parser.add_argument("--format", choices=["vertical", "carre"], default=None,
                        help="format de sortie : vertical 9:16 (défaut) ou carré 1:1")
    parser.add_argument("--cut-every", type=int, default=None, metavar="N",
                        help="force le mode fixe : coupe tous les N beats (défaut : coupes pilotées par l'énergie)")
    parser.add_argument("--subtitles", metavar="PREPROMPT", default=None,
                        help='génère des punchlines incrustées via le LLM (ex. "punchlines motivation gym, français, 5 mots max"). '
                             'Backend choisi par LLM_BACKEND : lmstudio (défaut, serveur local compatible OpenAI, coût nul) ou anthropic (requiert ANTHROPIC_API_KEY).')
    args = parser.parse_args()

    if not Path(args.track).is_file():
        sys.exit(f"morceau introuvable : {args.track}")
    if not Path(args.clips_dir).is_dir():
        sys.exit(f"dossier de clips introuvable : {args.clips_dir}")

    clips = load_clips(Path(args.clips_dir))
    print(f"Scan des plages exploitables ({len(clips)} clips)…")
    scan_clips(clips, cache_dir=Path("data/cache/scan"))

    config = load_settings()
    if args.cut_every is not None:
        config["cut_mode"] = "fixed"
        config["cut_every"] = args.cut_every
    if args.section is not None:
        config["section"] = args.section
    if args.format is not None:
        config["format"] = args.format
    if args.subtitles:
        config["subtitles"] = {**config["subtitles"], "enabled": True, "preprompt": args.subtitles}

    print("Génération…")
    generate_video(args.track, clips, config, seed=args.seed, output_path=args.output,
                   start=args.start, duration=args.duration,
                   subtitles_cache_dir=Path("data/cache/subtitles"), log=print)
    print(f"OK → {args.output}")


if __name__ == "__main__":
    main()
