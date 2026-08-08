"""speaker — cadrage du clipper : qui tient l'image, et comment on la découpe.

Deux sujets, une seule responsabilité : décider QUEL visage occupe le cadre à
chaque instant, et traduire cette décision en géométrie de crop pour FFmpeg.

La dépendance est à sens unique : `clipper` importe `speaker`, jamais l'inverse.
Un import croisé rendrait les deux modules inchargeables séparément, et c'est
`speaker` qui est le plus bas niveau des deux.

Comme dans `clipper`, le cœur est pur et testable — pistes, filtres, timeline,
segments — et seules la lecture vidéo et la détection OpenCV font de l'I/O.
"""

from pathlib import Path
from statistics import median

# Format de sortie du lot 1 : vertical en dur, pas de preset de format.
OUT_W, OUT_H = 1080, 1920

# Le cadre ne suit le visage que s'il s'en écarte de plus de cette fraction de
# la LARGEUR DU CROP (donc de la largeur de l'image finale, puisque le crop est
# étiré vers OUT_W). Sans zone morte, le crop corrige en permanence des
# micro-déplacements et le plan « respire » — c'est ce qui donne le mal de mer.
# L'unité compte : `smooth_track` reçoit des pixels SOURCE, et une zone morte
# exprimée en pixels de sortie vaudrait 21 % de l'image en 720p contre 7 % en
# 4K — le même invité se cadrerait différemment selon la définition du rush.
DEAD_ZONE = 0.08
# Fenêtre (impaire) de la moyenne glissante : à 2 fps, 5 échantillons lissent sur
# 2,5 s, assez pour écraser une fausse détection sans traîner sur un vrai
# changement de cadre.
SMOOTH_WINDOW = 5
# Plafond de paliers dans l'expression de crop. Au-delà, l'expression devient
# illisible et son évaluation par frame commence à coûter.
MAX_STEPS = 120


def _fill_holes(centers: list[float | None], default: float) -> list[float]:
    """Trous intérieurs interpolés, trous de bord tenus à la valeur connue la
    plus proche. Aucune détection du tout → `default` partout."""
    known = [i for i, c in enumerate(centers) if c is not None]
    if not known:
        return [default] * len(centers)
    out: list[float] = []
    for i in range(len(centers)):
        if centers[i] is not None:
            out.append(float(centers[i]))
            continue
        before = [k for k in known if k < i]
        after = [k for k in known if k > i]
        if not before:
            out.append(float(centers[after[0]]))
        elif not after:
            out.append(float(centers[before[-1]]))
        else:
            a, b = before[-1], after[0]
            t = (i - a) / (b - a)
            out.append(float(centers[a]) + t * (float(centers[b]) - float(centers[a])))
    return out


def smooth_track(centers: list[float | None], default: float,
                 dead_zone: float) -> list[float]:
    """Trajectoire de suivi exploitable : trous comblés, moyenne glissante, zone
    morte. Pure."""
    if not centers:
        return []
    filled = _fill_holes(centers, default)
    half = SMOOTH_WINDOW // 2
    averaged = [
        sum(filled[max(0, i - half):i + half + 1])
        / len(filled[max(0, i - half):i + half + 1])
        for i in range(len(filled))
    ]
    out = [averaged[0]]
    for value in averaged[1:]:
        out.append(out[-1] if abs(value - out[-1]) < dead_zone else value)
    return out


def _even(value: float) -> int:
    """Arrondi à l'entier pair inférieur : une dimension ou un offset impair
    produit des artefacts de chroma en yuv420p."""
    return int(value) - (int(value) % 2)


def crop_size(src_w: int, src_h: int) -> tuple[int, int]:
    """Rectangle 9:16 le plus large qui tienne dans la source. Une source déjà
    plus verticale que 9:16 n'est pas recadrée en largeur. Pure."""
    # src_h passe aussi par _even : une source de hauteur impaire (rare mais
    # existante) ferait échouer l'encodage yuv420p, donc un `failed` opaque.
    height = _even(src_h)
    width = min(src_w, _even(height * OUT_W / OUT_H))
    return (_even(width), height)


def _ramp(t0: float, x0: int, t1: float, x1: int) -> str:
    """Rampe linéaire de (t0, x0) à (t1, x1), en expression FFmpeg.

    Un palier sec ne « lisse » rien : le cadre reste figé le temps que la zone
    morte cède, puis saute de plusieurs pour cent de la largeur en une frame.
    C'est un saut de cadrage, pas un panoramique — d'où l'interpolation.
    `2*floor(…/2)` garde x pair, exigence du chroma yuv420p, au prix d'un
    escalier de 2 px invisible."""
    if x0 == x1:
        return str(x0)
    slope = (x1 - x0) / (t1 - t0)
    elapsed = "t" if t0 == 0 else f"(t-{t0:g})"
    return f"2*floor(({x0}+{slope:g}*{elapsed})/2)"


def _anchor(center: float, crop_w: int, src_w: int) -> int:
    """x du crop pour un visage centré en `center`, borné dans l'image et pair."""
    return _even(min(max(0.0, center - crop_w / 2), max(0, src_w - crop_w)))


def crop_segments(timeline: list[dict], tracks: list[dict], crop_w: int,
                  src_w: int) -> list[dict]:
    """Traduit la timeline en segments de cadrage. Un segment porte le x de son
    début et celui de sa fin : le cadre suit doucement l'orateur pendant un plan
    long, mais saute d'un segment à l'autre. Pure."""
    centre = _even(max(0, (src_w - crop_w) / 2))
    by_id = {t["id"]: t for t in tracks}
    segments = []
    for entry in timeline:
        track = by_id.get(entry["track_id"])
        if track is None or not track["boxes"]:
            segments.append({**{k: entry[k] for k in ("start", "end")},
                             "x_start": centre, "x_end": centre})
            continue
        indices = sorted(track["boxes"])
        first, last = track["boxes"][indices[0]], track["boxes"][indices[-1]]
        segments.append({
            "start": entry["start"], "end": entry["end"],
            "x_start": _anchor(first["x"] + first["w"] / 2, crop_w, src_w),
            "x_end": _anchor(last["x"] + last["w"] / 2, crop_w, src_w)})
    return segments


def track_to_segments(centers: list[float], sample_fps: float, crop_w: int,
                      src_w: int) -> list[dict]:
    """Convertit une trajectoire plate en segments d'une image chacun. Sert au
    chemin de repli (`speaker_cuts` désactivé), qui raisonne encore en
    trajectoire — `crop_expr` fusionnera les paliers identiques. Pure."""
    return [{"start": i / sample_fps, "end": (i + 1) / sample_fps,
             "x_start": _anchor(x, crop_w, src_w),
             "x_end": _anchor(x, crop_w, src_w)}
            for i, x in enumerate(centers)]


def crop_expr(segments: list[dict], crop_w: int, src_w: int) -> str:
    """Compile les segments en expression FFmpeg pour `crop=x=…`.

    Interpole À L'INTÉRIEUR d'un segment (l'orateur peut bouger pendant un plan
    long) et SAUTE SEC entre deux segments — c'est la coupe demandée, un
    glissement d'un visage à l'autre se lirait comme une dérive. Une suite
    entièrement immobile rend une constante, ce qui évite de demander une
    réévaluation par frame (voir `clipper.crop_supports_eval`). Pure."""
    centre = _even(max(0, (src_w - crop_w) / 2))
    if not segments:
        return str(centre)
    # Plafond de paliers : au-delà l'expression devient illisible et coûteuse.
    # Le dernier segment retenu est prolongé jusqu'à la fin plutôt que tronqué,
    # pour que le cadre ne reparte jamais au centre en cours de clip.
    if len(segments) > MAX_STEPS:
        segments = segments[:MAX_STEPS - 1] + [
            {**segments[MAX_STEPS - 1], "end": segments[-1]["end"]}]
    if all(s["x_start"] == s["x_end"] == segments[0]["x_start"]
           for s in segments):
        return str(segments[0]["x_start"])

    expr = _ramp(segments[-1]["start"], segments[-1]["x_start"],
                 segments[-1]["end"], segments[-1]["x_end"])
    for segment in reversed(segments[:-1]):
        inner = _ramp(segment["start"], segment["x_start"],
                      segment["end"], segment["x_end"])
        expr = f"if(lt(t,{segment['end']:g}),{inner},{expr})"
    return expr


# --- Suivi de visages -------------------------------------------------------

# Recouvrement minimal pour considérer que deux rectangles d'images successives
# sont le même visage. En dessous, deux visages voisins finiraient reliés dans
# la même piste — et le cadre passerait de l'un à l'autre sans qu'aucune coupe
# ne soit décidée.
IOU_MIN = 0.3

# Ancienneté maximale d'une piste (en images échantillonnées) avant qu'elle ne
# soit plus candidate à l'appariement. La cascade rate un visage pendant quelques
# images (tête tournée, flou de mouvement) — ce qui se compte en fractions de
# seconde, pas en dizaines. À 10 images/s (cadence d'échantillonnage prévue), 30
# images = 3 secondes, assez pour couvrir les occlusions légitimes sans risquer
# que deux personnes distinctes (une qui sort du cadre, une autre qui y arrive
# longtemps après au même endroit) se fusionnent sous la même identité.
TRACK_MAX_AGE = 30


def iou(a: dict, b: dict) -> float:
    """Recouvrement de deux rectangles, rapporté à leur union. Pure."""
    x1 = max(a["x"], b["x"])
    y1 = max(a["y"], b["y"])
    x2 = min(a["x"] + a["w"], b["x"] + b["w"])
    y2 = min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def link_tracks(detections: list[list[dict]], iou_min: float = IOU_MIN,
                max_gap: int = TRACK_MAX_AGE) -> list[dict]:
    """Relie les détections image par image en pistes persistantes.

    Une piste non appariée sur une image n'est pas close : la cascade rate
    régulièrement un visage (tête tournée, flou de mouvement), et rouvrir une
    piste ferait passer la même personne pour une nouvelle — donc une coupe
    injustifiée. Elle reste donc candidate à l'appariement sur sa DERNIÈRE
    position connue — tant que cette position n'est pas trop ancienne. Pure."""
    tracks: list[dict] = []
    for index, boxes in enumerate(detections):
        # Appariement glouton par recouvrement décroissant : le meilleur couple
        # est figé d'abord, ce qui évite qu'un visage vole l'appariement d'un
        # autre quand deux personnes se rapprochent. Les pistes plus anciennes que
        # max_gap images ne sont pas candidates : sans cette borne, une personne
        # qui sort du cadre puis un autre visage au même endroit fusionneraient.
        last_seen = {t: max(track["boxes"]) for t, track in enumerate(tracks)}
        pairs = sorted(
            ((iou(track["boxes"][last_seen[t]], box), t, d)
             for t, track in enumerate(tracks)
             if index - last_seen[t] <= max_gap
             for d, box in enumerate(boxes)),
            key=lambda p: (-p[0], p[1], p[2]))
        used_tracks: set[int] = set()
        used_boxes: set[int] = set()
        for score, t, d in pairs:
            if score < iou_min or t in used_tracks or d in used_boxes:
                continue
            tracks[t]["boxes"][index] = boxes[d]
            used_tracks.add(t)
            used_boxes.add(d)
        for d, box in enumerate(boxes):
            if d not in used_boxes:
                tracks.append({"id": len(tracks), "boxes": {index: box},
                               "activity": {}})
    return tracks


# Un visage sous cette fraction de la hauteur d'image n'est pas un interlocuteur
# cadré mais une vignette — sur la source d'essai, trois « visages » de 70 px
# sur 1080 étaient de l'habillage collé au bord. On juge sur la MÉDIANE des
# hauteurs, pas le maximum, pour qu'un seul faux positif de grande taille ne
# sauve pas une piste composée à 90 % de vignettes — cas réel : dix rectangles
# à h=60 plus un à h=300 varié. La médiane rejette la piste ; le max l'aurait
# gardée. Un visage qui s'approche (petit puis grand) reste gardé dès qu'il est
# grand sur la moitié de sa piste.
MIN_FACE_FRACTION = 0.06
# Déplacement en dessous duquel une piste est jugée parfaitement immobile. Une
# personne vivante bouge toujours de plus de deux pixels ; ce qui n'en bouge pas
# est incrusté dans l'image.
STATIC_TOLERANCE = 2
# En deçà de ce nombre d'images, l'immobilité ne prouve rien : on ne rejette pas
# une piste sur un échantillon trop court.
_STATIC_MIN_FRAMES = 8


def usable_tracks(tracks: list[dict], frame_h: int) -> list[dict]:
    """Ne garde que les pistes qui peuvent être un interlocuteur qui parle.
    Conserve l'ordre d'entrée. Pure."""
    kept = []
    for track in tracks:
        boxes = list(track["boxes"].values())
        if not boxes:
            continue
        # Trop petit : une vignette, pas quelqu'un de cadré. Médiane, pas max :
        # un seul faux positif de grande taille ne doit pas sauver une piste
        # composée de vignettes.
        if median(box["h"] for box in boxes) < MIN_FACE_FRACTION * frame_h:
            continue
        # Jamais agité : une affiche, un visage de dos, un faux positif. Un dict
        # activity vide est un cas réel (visage vu seulement à t=0) : sans mesure
        # comparant avec l'image précédente, rien ne prouve qu'il parle.
        if not track["activity"] or not any(value > 0 for value in track["activity"].values()):
            continue
        # Parfaitement immobile sur une durée significative : de l'habillage.
        if len(boxes) >= _STATIC_MIN_FRAMES:
            xs = [box["x"] for box in boxes]
            ys = [box["y"] for box in boxes]
            if (max(xs) - min(xs) <= STATIC_TOLERANCE
                    and max(ys) - min(ys) <= STATIC_TOLERANCE):
                continue
        kept.append(track)
    return kept


# --- Découpage en plans -----------------------------------------------------

# Fenêtre glissante de mesure de l'agitation : assez longue pour lisser une
# syllabe, assez courte pour réagir à une prise de parole.
ACTIVITY_WINDOW = 0.6
# Le prétendant doit être une fois et demie plus agité que le tenant. En deçà,
# deux personnes qui se coupent la parole feraient osciller le cadre.
SWITCH_MARGIN = 1.5
# Durée minimale d'un plan. Dans une conversation vive la parole alterne en
# moins d'une seconde ; sans plancher, le cadre ferait des allers-retours qui se
# lisent comme un bug et non comme un montage.
MIN_SHOT = 1.2
# Plancher réduit qui s'applique quand même sur une coupe de la source. La
# coupe rend le SAUT invisible, mais pas un plan d'un dixième de seconde
# regardable : sans ce second plancher, une rafale de coupes rapprochées (ou
# même une seule plage de coupes) désactiverait complètement MIN_SHOT.
CUT_MIN_SHOT = 0.4


def _windowed_activity(track: dict, index: int, half: int) -> float:
    """Agitation moyenne d'une piste autour d'une image. Une image sans mesure
    compte pour zéro : un visage non détecté ne parle pas, de notre point de vue.
    `half >= 1`, donc la fenêtre contient toujours au moins 3 échantillons."""
    values = [track["activity"].get(i, 0.0)
              for i in range(index - half, index + half + 1)]
    return sum(values) / len(values)


def speaker_timeline(tracks: list[dict], cuts: set[int], n_frames: int,
                     fps: float, min_shot: float = MIN_SHOT) -> list[dict]:
    """Qui tient le cadre, à chaque instant. Segments contigus couvrant tout le
    clip. `track_id` ne vaut None que s'il n'y a AUCUNE piste candidate
    (cadrage centré) : dès qu'une piste existe, on tient toujours le dernier
    cadrage plutôt que de recentrer — y compris en silence total, où recentrer
    se lirait comme une panne. Pure."""
    if n_frames <= 0:
        return []
    if not tracks:
        return [{"start": 0.0, "end": n_frames / fps, "track_id": None}]

    half = max(1, int(round(ACTIVITY_WINDOW * fps / 2)))
    min_frames = max(1, int(round(min_shot * fps)))
    cut_min_frames = max(1, int(round(CUT_MIN_SHOT * fps)))
    current = None
    since = 0
    # Le plancher ne protège qu'un cadrage choisi en connaissance de cause. Le
    # tout premier choix, à `index == 0`, peut être fait à l'aveugle (silence
    # total : le départage `-k` élit alors arbitrairement l'identifiant le
    # plus petit) — ce choix ne doit pas verrouiller le plan pendant min_shot,
    # sans quoi le premier qui parle vraiment serait tenu à l'écart. `armed`
    # ne passe à True qu'au moment d'un choix appuyé sur un score positif.
    armed = False
    boundaries: list[tuple[int, int | None]] = []
    for index in range(n_frames):
        scores = {t["id"]: _windowed_activity(t, index, half) for t in tracks}
        best = max(scores, key=lambda k: (scores[k], -k))
        if current is None:
            current, since = best, index
            armed = scores[best] > 0
            boundaries.append((index, current))
            continue
        if not armed:
            libre = True
        elif index in cuts:
            libre = (index - since) >= cut_min_frames
        else:
            libre = (index - since) >= min_frames
        # Marge de bascule : le tenant garde la main tant qu'il n'est pas
        # nettement dépassé. Si tous les scores sont nuls (silence total),
        # personne n'est dépassé : on tient plutôt que de recentrer.
        domine = scores[best] > SWITCH_MARGIN * scores[current] and scores[best] > 0
        if best != current and libre and domine:
            current, since = best, index
            armed = True
            boundaries.append((index, current))

    segments = []
    for (start_index, track_id), (next_index, _) in zip(
            boundaries, boundaries[1:] + [(n_frames, None)]):
        segments.append({"start": start_index / fps, "end": next_index / fps,
                         "track_id": track_id})
    return segments
