"""beatsync — montage vidéo vertical synchronisé sur les beats d'un morceau.

Pipeline : analyze_audio -> load_clips -> build_edl (logique pure) -> render.
"""

import argparse
import hashlib
import json
import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}

DEFAULT_CONFIG = {
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
    "strobe_beats": 16,                 # coupes forcées à 1 beat après le drop
    "effects": {"zoom": True, "flash": True, "shake": True, "speed": True},
    "chrono": True,                     # extraits en ordre chronologique dans l'histoire du clip
    "min_presence": 0.3,                # score minimal « personnages à l'écran » d'une plage
    "accents": {"rgb": True, "glitch": True},  # RGB split à l'impact, micro-glitch temps forts
    "subtitles": {                      # punchlines incrustées, générées par Claude
        "enabled": False,               # désactivé par défaut
        "preprompt": "",                # consigne de style (ex. « punchlines motivation gym »)
        "min_dur": 1.4,                 # durée min. d'affichage d'une punchline (lisibilité)
        "model": "claude-opus-4-8",     # modèle de génération
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
    """Métadonnées des clips vidéo du dossier, triés par nom (déterminisme)."""
    clips = []
    for path in sorted(Path(folder).iterdir()):
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
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
                "duration": float(info["format"]["duration"]),
                "width": width,
                "height": height,
                "ratio": width / height,
            }
        )
    if not clips:
        raise ValueError(f"aucun clip vidéo ({', '.join(sorted(VIDEO_EXTENSIONS))}) dans {folder}")
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


SCAN_FPS = 2.0
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


def _scan_payload(clip: dict) -> dict:
    return {
        "intervals": clip["intervals"],
        "interest_x": [float(x) for x in clip["interest_x"]],
        "dual": [bool(d) for d in clip["dual"]],
        "scan_dt": clip["scan_dt"],
    }


def _apply_scan_payload(clip: dict, payload: dict) -> None:
    clip["intervals"] = payload["intervals"]
    clip["interest_x"] = np.array(payload["interest_x"], dtype=float)
    clip["dual"] = np.array(payload["dual"], dtype=bool)
    clip["scan_dt"] = payload["scan_dt"]


def scan_clips(clips: list[dict], cache_dir: Path | None = None) -> list[dict]:
    """Enrichit chaque clip de ses plages exploitables : cartes orange, noir et
    passages statiques exclus, score de présence des personnages par plage.
    Avec cache par fichier (clé md5 du chemin, invalidé par mtime) quand
    cache_dir est fourni — on ne re-décode pas 30 clips à chaque génération."""
    for clip in clips:
        cache_path = None
        if cache_dir is not None:
            digest = hashlib.md5(str(clip["path"]).encode()).hexdigest()
            cache_path = cache_dir / f"{digest}.json"
            if cache_path.is_file():
                # Cache tronqué/corrompu (process tué en pleine écriture) :
                # traité comme un miss, on re-scanne et on réécrit le cache.
                try:
                    cached = json.loads(cache_path.read_text())
                    if cached.get("mtime") == clip["path"].stat().st_mtime:
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

    start=None => cadrage auto : buildup avant le drop détecté (ou début du
    morceau sans drop net). duration="full" => tout le morceau ; sinon la fin
    est étendue à la frontière de phrase suivante.
    """
    drop = find_drop(analysis, config)
    config["drop_time"] = drop
    if start is None:
        start = max(0.0, drop - config["buildup"]) if drop is not None else 0.0
    config["start"] = float(start)
    if duration == "full":
        config["end"] = float(analysis["duration"])
    else:
        end = min(config["start"] + float(duration), float(analysis["duration"]))
        config["end"] = snap_end_to_phrase(
            end, drop, analysis["beats"], analysis["duration"], config["phrase_beats"]
        )
    return config


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

    def intervals_of(clip: dict) -> list[dict]:
        if "intervals" not in clip:  # pas scanné : clip entier utilisable
            return [{"start": 0.0, "end": clip["duration"], "motion": 1.0}]
        return clip["intervals"]  # scanné ([] = rien d'exploitable, clip exclu)

    # --- Attribution des clips : tirage seedé dans les plages exploitables ---
    edl: list[dict] = []
    prev_path = None
    drop_seg_count = 0
    last_clip_in: dict = {}  # par clip : dernier point d'entrée (mode chrono)
    for (seg_start, beat_index), (seg_end, _) in zip(boundaries, boundaries[1:]):
        duration = seg_end - seg_start
        tier = tier_at(beat_index if beat_index >= 0 else (int(in_window[0]) if len(in_window) else 0))
        if drop_out is None:
            section = "main"
        else:
            section = "buildup" if seg_start < drop_out - 1e-9 else "drop"

        # Gasp : slow-mo x0.5 sur le dernier segment avant l'impact du drop.
        speed = 1.0
        if effects_cfg.get("speed") and drop_out is not None and section == "buildup" \
                and abs(seg_end - drop_out) < 1e-9:
            speed = 0.5

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
            if accents.get("glitch") and drop_seg_count > 0 and tier == "intense" \
                    and rng.random() < 0.25:
                effects.append("glitch")
            drop_seg_count += 1
        elif effects_cfg.get("shake") and tier == "intense" and rng.random() < 0.3:
            effects.append("shake")

        source_needed = duration * speed
        usable = [
            c for c in clips
            if any(iv["end"] - iv["start"] >= source_needed for iv in intervals_of(c))
        ]
        if not usable:
            raise ValueError(
                f"aucun clip n'a de plage exploitable de {source_needed:.2f}s "
                "(clips trop courts, ou trop de zones écartées par le scan)"
            )
        pool = [c for c in usable if c["path"] != prev_path] or usable
        clip = rng.choice(pool)

        candidates = [iv for iv in intervals_of(clip) if iv["end"] - iv["start"] >= source_needed]
        # Personnages à l'écran : écarte les plages quasi vides (fallback si toutes le sont).
        min_presence = config.get("min_presence", 0.0)
        candidates = [iv for iv in candidates if iv.get("presence", 1.0) >= min_presence] or candidates

        if config.get("chrono", False):
            # Position dans la vidéo ≈ position dans l'histoire : le montage
            # avance dans le clip au rythme de la timeline (climax au drop).
            progress = seg_start / out_end if out_end > 0 else 0.0
            slacks = [iv["end"] - iv["start"] - source_needed for iv in candidates]
            target = progress * sum(slacks)
            interval, offset = candidates[-1], slacks[-1]
            for iv, slack in zip(candidates, slacks):
                if target <= slack:
                    interval, offset = iv, target
                    break
                target -= slack
            clip_in = interval["start"] + offset + rng.uniform(0.0, 1.0)
            clip_in = max(clip_in, last_clip_in.get(clip["path"], 0.0) + 0.1)
            clip_in = min(max(clip_in, interval["start"]), interval["end"] - source_needed)
            last_clip_in[clip["path"]] = clip_in
        else:
            if (section == "drop" or tier == "intense") and len(candidates) > 1:
                # Les moments intenses piochent dans les plages les plus nerveuses.
                median_motion = float(np.median([iv["motion"] for iv in candidates]))
                candidates = [iv for iv in candidates if iv["motion"] >= median_motion] or candidates
            interval = rng.choice(candidates)
            clip_in = rng.uniform(interval["start"], interval["end"] - source_needed)
        prev_path = clip["path"]

        # Cadrage : centre d'intérêt et layout, moyennés sur l'extrait choisi.
        focus_x, layout = 0.5, "crop"
        if "interest_x" in clip:
            dt = clip.get("scan_dt", 1.0 / SCAN_FPS)
            i0 = int(clip_in / dt)
            # Au moins 3 échantillons (1,5 s) : un extrait d'un beat n'en couvre
            # parfois qu'un seul, trop peu pour juger dispersion et duel.
            i1 = max(i0 + 3, math.ceil((clip_in + source_needed) / dt))
            window_x = np.asarray(clip["interest_x"], dtype=float)[i0:i1]
            if len(window_x):
                focus_x = float(np.clip(window_x.mean(), 0.0, 1.0))
                dual = np.asarray(clip.get("dual", []), dtype=bool)[i0:i1]
                if len(dual) and float(dual.mean()) >= 0.5:
                    layout = "split"   # duel : deux moitiés empilées haut/bas
                elif float(window_x.std()) >= 0.18:
                    layout = "blur"    # action sur toute la largeur : plan entier sur fond flouté

        edl.append(
            {
                "timeline_start": seg_start,
                "duration": duration,
                "clip_path": clip["path"],
                "clip_in": clip_in,
                "beat_index": beat_index,
                "section": section,
                "speed": speed,
                "effects": effects,
                "focus_x": focus_x,
                "layout": layout,
                "clip_w": clip["width"],
                "clip_h": clip["height"],
            }
        )
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


def _drawtext_escape(text: str) -> str:
    """Échappe le texte pour l'option drawtext de FFmpeg (argument non shell)."""
    out = text.replace("\\", "\\\\")
    for ch in (":", "'", "%", ",", ";", "[", "]"):
        out = out.replace(ch, "\\" + ch)
    return out


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


def _call_llm(preprompt: str, count: int, seed: int, model: str) -> list[str]:
    """Appelle Claude pour générer `count` punchlines. Sortie JSON structurée.
    Isolé pour être mocké dans les tests."""
    import anthropic

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
        system=("Tu écris des punchlines courtes et percutantes incrustées sur des edits "
                "vidéo verticaux. Chaque punchline fait 2 à 6 mots, sans hashtag, sans emoji, "
                "sans ponctuation finale, et forme une progression cohérente d'une à l'autre."),
        messages=[{"role": "user", "content":
                   f"Génère exactement {count} punchlines distinctes.\n"
                   f"Style / consigne : {preprompt}\nVariation n°{seed}."}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return [str(p) for p in json.loads(text)["punchlines"]]


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
        key = hashlib.md5(f"{model}|{preprompt}|{count}|{seed}".encode()).hexdigest()
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
    n = assign_caption_slots(edl, float(sub.get("min_dur", 1.4)))
    lines = generate_punchlines(sub.get("preprompt", ""), n, seed, cache_dir,
                                sub.get("model", "claude-opus-4-8"))
    for entry in edl:
        i = entry.get("caption_slot", 0)
        entry["caption"] = lines[i] if i < len(lines) else ""
    return edl


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg a échoué :\n  ffmpeg {' '.join(args)}\n{result.stderr}")


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
    if config.get("delogo") and "clip_w" in entry:
        # Gomme le logo de chaîne (coin haut-gauche) AVANT recadrage : le
        # recadrage intelligent ou le fond flouté peuvent le faire entrer au champ.
        cw, ch = entry["clip_w"], entry["clip_h"]
        pre += (f"delogo=x={max(1, int(cw * 0.01))}:y={max(1, int(ch * 0.02))}"
                f":w={int(cw * 0.22)}:h={int(ch * 0.10)},")
    if speed != 1.0:
        pre += f"setpts=(PTS-STARTPTS)/{speed:.6f},"

    # --- Chaîne commune post-layout (opère sur du 1080x1920) ---
    post = [f"fps={fps}"]
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
    # Punchline incrustée (après les accents pour rester nette), bas-centrée
    cap = entry.get("caption")
    font = _caption_font()
    if cap and font:
        post.append(
            f"drawtext=fontfile={font}:text={_drawtext_escape(cap)}"
            ":fontsize=64:fontcolor=white:borderw=5:bordercolor=black@0.9"
            ":x=(w-text_w)/2:y=h*0.70"
        )
    post.append("setsar=1,format=yuv420p")
    post.append("tpad=stop_mode=clone:stop_duration=1")
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
            source_needed = entry["duration"] * entry.get("speed", 1.0)
            _run_ffmpeg(
                [
                    "-ss", f"{entry['clip_in']:.6f}",  # avant -i : seek rapide
                    "-t", f"{source_needed + 0.5:.6f}",
                    "-i", str(entry["clip_path"]),
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
        description="Montage vidéo vertical 9:16 synchronisé sur les beats d'un morceau."
    )
    parser.add_argument("track", help="chemin du morceau audio")
    parser.add_argument("clips_dir", help="dossier des clips vidéo")
    parser.add_argument("-o", "--output", default="output.mp4", help="fichier de sortie (défaut : output.mp4)")
    parser.add_argument("--seed", type=int, default=42, help="graine de reproductibilité (défaut : 42)")
    parser.add_argument("--start", type=float, default=None,
                        help="début manuel de la fenêtre, en s (défaut : cadrage auto buildup + drop)")
    parser.add_argument("--duration", default="30", help='durée de la fenêtre en s, ou "full" (défaut : 30)')
    parser.add_argument("--cut-every", type=int, default=None, metavar="N",
                        help="force le mode fixe : coupe tous les N beats (défaut : coupes pilotées par l'énergie)")
    parser.add_argument("--subtitles", metavar="PREPROMPT", default=None,
                        help='génère des punchlines incrustées via Claude (ex. "punchlines motivation gym, français, 5 mots max"). Requiert ANTHROPIC_API_KEY.')
    args = parser.parse_args()

    if not Path(args.track).is_file():
        sys.exit(f"morceau introuvable : {args.track}")
    if not Path(args.clips_dir).is_dir():
        sys.exit(f"dossier de clips introuvable : {args.clips_dir}")

    print(f"Analyse de {args.track}…")
    analysis = analyze_audio(Path(args.track))
    print(f"  {analysis['bpm']:.1f} BPM, {len(analysis['beats'])} beats, {analysis['duration']:.1f} s")

    if args.start is not None and args.start >= analysis["duration"]:
        sys.exit(f"--start {args.start} dépasse la durée du morceau ({analysis['duration']:.1f} s)")
    config = resolve_window(analysis, load_settings(), start=args.start, duration=args.duration)
    drop = config["drop_time"]
    print(f"  drop détecté à {drop:.1f} s" if drop is not None else "  pas de drop net détecté")
    print(f"  fenêtre : {config['start']:.1f} → {config['end']:.1f} s "
          f"({config['end'] - config['start']:.1f} s, fin sur phrase)")
    if args.cut_every is not None:
        config["cut_mode"] = "fixed"
        config["cut_every"] = args.cut_every

    clips = load_clips(Path(args.clips_dir))
    print(f"  {len(clips)} clips dans {args.clips_dir}, scan des plages exploitables…")
    scan_clips(clips)
    for clip in clips:
        usable_s = sum(iv["end"] - iv["start"] for iv in clip["intervals"])
        with_chars = sum(
            iv["end"] - iv["start"] for iv in clip["intervals"]
            if iv["presence"] >= config["min_presence"]
        )
        print(f"    {clip['path'].name} : {usable_s:.0f} s exploitables / {clip['duration']:.0f} s "
              f"({len(clip['intervals'])} plage(s), {with_chars:.0f} s avec personnages)")

    edl = build_edl(analysis, clips, config, seed=args.seed)
    n_fx = sum(bool(e["effects"]) for e in edl)
    print(f"  EDL : {len(edl)} segments sur {config['end'] - config['start']:.1f} s "
          f"(seed {args.seed}, {n_fx} segments avec effets)")

    if args.subtitles:
        config["subtitles"] = {**config["subtitles"], "enabled": True, "preprompt": args.subtitles}
        print("  génération des punchlines (Claude)…")
        apply_subtitles(edl, config, seed=args.seed, cache_dir=Path("data/cache/subtitles"))
        n_cap = len({e.get("caption") for e in edl if e.get("caption")})
        print(f"  {n_cap} punchline(s) distincte(s)" if n_cap
              else "  aucune punchline (pas de clé API ? rendu sans texte)")

    print("Rendu FFmpeg…")
    render(edl, Path(args.track), Path(args.output), config)
    print(f"OK → {args.output}")


if __name__ == "__main__":
    main()
