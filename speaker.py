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


def _nearest_box(boxes: dict[int, dict], start_frame: int, end_frame: int) -> dict:
    """Rectangle de la piste le plus proche en numéro d'image de l'intervalle
    `[start_frame, end_frame)`. Sert à combler un trou de détection : la
    cascade a raté le visage sur ce plan précis, mais la position connue la
    plus proche dans le temps reste le meilleur indice de où il est."""
    def distance(i: int) -> int:
        return min(abs(i - start_frame), abs(i - (end_frame - 1)))
    nearest = min(boxes, key=lambda i: (distance(i), i))
    return boxes[nearest]


def crop_segments(timeline: list[dict], tracks: list[dict], crop_w: int,
                  src_w: int, fps: float) -> list[dict]:
    """Traduit la timeline en segments de cadrage. Un segment porte le x de son
    début et celui de sa fin : le cadre suit doucement l'orateur pendant un plan
    long, mais saute d'un segment à l'autre. Pure.

    Trois niveaux de repli, dans cet ordre :
    1. des rectangles DANS le segment (numéro d'image compris dans ses bornes,
       converties via `fps`, l'unité dans laquelle `track["boxes"]` est
       indexé) → cadrage normal, ancré sur le premier et le dernier.
    2. aucun rectangle dans le segment mais la piste en a AILLEURS dans le
       clip → trou de détection (link_tracks tolère jusqu'à TRACK_MAX_AGE
       images sans appariement au sein d'une même piste, et une frontière de
       `speaker_timeline` peut tomber à quelques images d'une détection réelle
       à cause de la fenêtre glissante) : la personne est toujours là, on
       tient sa position connue la plus proche plutôt que de recentrer.
       `speaker_timeline` refuse déjà ce recentrage en plein clip (« ça se lit
       comme une panne ») ; le cadrage géométrique ne doit pas la contredire.
    3. piste inconnue, ou piste sans le moindre rectangle → centre : là, il
       n'y a rien à tenir."""
    centre = _even(max(0, (src_w - crop_w) / 2))
    by_id = {t["id"]: t for t in tracks}
    segments = []
    for entry in timeline:
        track = by_id.get(entry["track_id"])
        if track is None or not track["boxes"]:
            segments.append({"start": entry["start"], "end": entry["end"],
                             "x_start": centre, "x_end": centre})
            continue
        start_frame = round(entry["start"] * fps)
        end_frame = round(entry["end"] * fps)
        in_segment = sorted((i, box) for i, box in track["boxes"].items()
                            if start_frame <= i < end_frame)
        if in_segment:
            first, last = in_segment[0][1], in_segment[-1][1]
            segments.append({
                "start": entry["start"], "end": entry["end"],
                "x_start": _anchor(first["x"] + first["w"] / 2, crop_w, src_w),
                "x_end": _anchor(last["x"] + last["w"] / 2, crop_w, src_w)})
            continue
        nearest = _nearest_box(track["boxes"], start_frame, end_frame)
        x = _anchor(nearest["x"] + nearest["w"] / 2, crop_w, src_w)
        segments.append({"start": entry["start"], "end": entry["end"],
                         "x_start": x, "x_end": x})
    return segments


def track_to_segments(centers: list[float], sample_fps: float, crop_w: int,
                      src_w: int) -> list[dict]:
    """Convertit une trajectoire plate en segments d'une image chacun. Sert au
    chemin de repli (`speaker_cuts` désactivé), qui raisonne encore en
    trajectoire. Le x_end de chaque segment est l'ancre de l'échantillon
    SUIVANT, pas celle du même échantillon : sinon chaque segment est immobile
    (x_start == x_end) et `crop_expr` ne ferait qu'un escalier de paliers secs
    — exactement ce que `_ramp` existe pour éviter. Le dernier segment n'a pas
    de suivant : son x_end vaut sa propre ancre, ce qui tient la valeur.
    `crop_expr` fusionnera ensuite les segments immobiles consécutifs. Pure."""
    anchors = [_anchor(x, crop_w, src_w) for x in centers]
    return [{"start": i / sample_fps, "end": (i + 1) / sample_fps,
             "x_start": anchors[i],
             "x_end": anchors[i + 1] if i + 1 < len(anchors) else anchors[i]}
            for i in range(len(anchors))]


def _merge_static(segments: list[dict]) -> list[dict]:
    """Fusionne les segments adjacents immobiles et de même ancre : deux plans
    fixes consécutifs au même endroit ne valent pas deux paliers dans
    l'expression FFmpeg — inutile, et ça consomme le plafond `MAX_STEPS` pour
    rien (cas réel : `track_to_segments` à 2 images/s produit un segment par
    image, dont l'immense majorité est immobile). Pure."""
    merged: list[dict] = []
    for segment in segments:
        static = segment["x_start"] == segment["x_end"]
        if (static and merged and merged[-1]["x_start"] == merged[-1]["x_end"]
                == segment["x_start"]):
            merged[-1] = {**merged[-1], "end": segment["end"]}
        else:
            merged.append(segment)
    return merged


def expr_needs_eval(expr: str) -> bool:
    """Dit si l'expression de crop dépend du temps.

    La présence d'un `if(` ne suffit PLUS à en juger, dans un sens comme dans
    l'autre : un segment unique non constant (« le visage dérive pendant tout
    le plan ») compile en une simple rampe `2*floor((…+…*t)/2)` SANS aucun
    `if(`, et pourtant dépend bien de `t` ; à l'inverse, rien n'empêcherait un
    jour une expression de contenir `if(` sans dépendre du temps. Le seul test
    fiable : `crop_expr` ne rend jamais que deux formes — un entier littéral
    (cadre figé) ou une expression qui référence `t`. Pure."""
    try:
        int(expr)
        return False
    except ValueError:
        return True


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
    segments = _merge_static(segments)
    # Plafond de paliers : au-delà l'expression devient illisible et coûteuse.
    # Le dernier segment retenu est prolongé jusqu'à la fin plutôt que tronqué,
    # pour que le cadre ne reparte jamais au centre en cours de clip.
    if len(segments) > MAX_STEPS:
        segments = segments[:MAX_STEPS - 1] + [
            {**segments[MAX_STEPS - 1], "end": segments[-1]["end"]}]
    if all(s["x_start"] == s["x_end"] == segments[0]["x_start"]
           for s in segments):
        return str(segments[0]["x_start"])

    # Après la fin du DERNIER segment, le cadre tient sa valeur finale plutôt
    # que de continuer la rampe : ffmpeg évalue l'expression pour tout t, y
    # compris au-delà de la fin de la timeline (arrondis de durée rendue), et
    # une rampe non bornée extrapolerait hors de l'image.
    last = segments[-1]
    expr = str(last["x_end"])
    if last["x_start"] != last["x_end"]:
        inner = _ramp(last["start"], last["x_start"], last["end"], last["x_end"])
        expr = f"if(lt(t,{last['end']:g}),{inner},{expr})"
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


# Nombre de PASSES DE DÉTECTION distinctes qu'une piste doit compter pour être
# prise au sérieux. Attention à l'unité : entre deux passes, les rectangles sont
# tenus à leur dernière position, donc une piste vue une seule fois porte quand
# même `DETECT_EVERY × FRAME_FPS` images. Compter les images laissait passer tous
# les faux positifs — mesuré sur la fenêtre 60-90 s de la source d'essai : 39
# pistes, dont 26 vues sur une seule passe, et zéro rejetée. Une personne à
# l'écran est revue à la passe suivante ; un faux positif Haar, presque jamais.
MIN_DETECTIONS = 2

# Un visage sous cette fraction de la hauteur d'image n'est pas un interlocuteur
# cadré mais une vignette — sur la source d'essai, trois « visages » de 70 px
# sur 1080 étaient de l'habillage collé au bord. On juge sur la MÉDIANE des
# hauteurs, pas le maximum, pour qu'un seul faux positif de grande taille ne
# sauve pas une piste composée à 90 % de vignettes — cas réel : dix rectangles
# à h=60 plus un à h=300 varié. La médiane rejette la piste ; le max l'aurait
# gardée. Un visage qui s'approche (petit puis grand) reste gardé dès qu'il est
# grand sur la moitié de sa piste.
MIN_FACE_FRACTION = 0.06
# Déplacement, RAPPORTÉ À LA TAILLE DU RECTANGLE, en dessous duquel une piste est
# jugée immobile — donc incrustée dans l'image. Une tolérance absolue ne peut pas
# servir les deux échelles : 10 px sur une vignette de 70 px, c'est de
# l'habillage ; 10 px sur un visage de 400 px, c'est un frémissement. Mesuré sur
# la source d'essai : les trois faux visages de l'habillage (140-380 s) se
# déplacent de 2,6 %, 5,9 % et 10,2 % de leur hauteur, tandis que le vrai
# interlocuteur le plus statique de la fenêtre 60-90 s en parcourt 19 %. Le seuil
# est posé au milieu de cet écart.
STATIC_FRACTION = 0.15
# En deçà de ce nombre de passes de détection, l'immobilité ne prouve rien : sur
# une demi-seconde, un invité assis peut très bien ne pas parcourir 15 % de la
# hauteur de son visage. On compte les passes et non les images — les images
# tenues entre deux passes sont immobiles par construction et gonfleraient le
# compte sans rien prouver (c'est ce que faisait l'ancien `_STATIC_MIN_FRAMES`).
_STATIC_MIN_DETECTIONS = 4


def usable_tracks(tracks: list[dict], frame_h: int) -> list[dict]:
    """Ne garde que les pistes qui peuvent être un interlocuteur qui parle.
    Conserve l'ordre d'entrée. Pure."""
    kept = []
    for track in tracks:
        boxes = list(track["boxes"].values())
        if not boxes:
            continue
        # Vue une seule fois : la cascade Haar produit un faux positif par-ci
        # par-là, jamais deux fois de suite au même endroit. Les rectangles tenus
        # entre deux passes ne comptent pas — ce sont des copies, pas des preuves.
        n_detections = sum(1 for box in boxes if box.get("detected"))
        if n_detections < MIN_DETECTIONS:
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
        # Immobile sur une durée significative, à l'échelle de sa propre taille :
        # de l'habillage. La comparaison se fait sur la hauteur médiane du
        # rectangle, la même grandeur que celle du filtre de taille ci-dessus.
        if n_detections >= _STATIC_MIN_DETECTIONS:
            xs = [box["x"] for box in boxes]
            ys = [box["y"] for box in boxes]
            tolerance = STATIC_FRACTION * median(box["h"] for box in boxes)
            if (max(xs) - min(xs) <= tolerance
                    and max(ys) - min(ys) <= tolerance):
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


# --- I/O : lecture, détection, mesure ---------------------------------------

# La parole agite la bouche à 5-10 Hz : en dessous de 10 images/s, l'information
# n'existe tout simplement pas. C'est abordable parce qu'on décode le clip d'une
# traite — une lecture séquentielle coûte 1,4 ms l'image contre 45 ms pour un
# seek, mesuré sur la source d'essai.
FRAME_FPS = 10.0
# Une tête ne se déplace pas assez en un demi-quart de seconde pour justifier de
# repayer une détection Haar : entre deux détections, on tient les rectangles à
# leur dernière position et on n'y mesure que l'agitation de la bouche.
DETECT_EVERY = 0.5
# Haar en 960x540 coûte 8 ms contre 25 ms en pleine résolution, pour la même
# détection à ces tailles de visage.
DETECT_SCALE = 0.5
# Une coupe du montage d'origine est un écart inter-images qui SORT DE LA
# DISTRIBUTION du clip, pas qui dépasse une valeur absolue : un seuil fixe ne peut
# pas servir à la fois un plateau fixe et une captation agitée. Mesuré sur la
# source d'essai — fenêtre 60-90 s (caméra à l'épaule, écart médian 0,037) : les
# trois vraies coupes valent 0,217 / 0,189 / 0,196, et les pics de flou de
# filé montent jusqu'à 0,170. Le rapport les sépare (5 × 0,037 = 0,185), un seuil
# absolu bas ne le ferait pas.
CUT_RATIO = 5.0
# Plancher absolu, indispensable quand le clip est calme : fenêtre 150-180 s
# (longs plans fixes, écart médian 0,017), où 5 × médiane = 0,084 relèverait 17
# candidats dont 10 faux. Les vraies coupes y valent 0,177 à 0,199 et le pire
# flou de filé 0,125 : le plancher est posé entre les deux. Il coûte une vraie
# coupe sur huit (une transition douce à 0,129), ce qui est le bon compromis —
# une coupe manquée laisse simplement `MIN_SHOT` s'appliquer, alors qu'une fausse
# coupe autorise un plan de 0,4 s là où aucun saut du montage ne le masque.
CUT_FLOOR = 0.15


def detect_cuts(diffs: list[float], ratio: float = CUT_RATIO,
                floor: float = CUT_FLOOR) -> set[int]:
    """Indices d'images où le montage d'origine a coupé. `diffs[i]` est l'écart
    entre l'image i-1 et l'image i, en fraction de la dynamique ; `diffs[0]` n'a
    pas de sens (aucune image ne précède la première) et ne participe donc ni au
    calcul de la médiane ni au relevé. Pure."""
    if len(diffs) < 2:
        return set()
    threshold = max(ratio * median(diffs[1:]), floor)
    return {i for i in range(1, len(diffs)) if diffs[i] > threshold}


def _mouth_activity(previous, current, box: dict, scale: float) -> float:
    """Agitation du tiers inférieur d'un rectangle entre deux images réduites."""
    x = int(box["x"] * scale)
    y = int((box["y"] + box["h"] * 2 / 3) * scale)
    w = max(1, int(box["w"] * scale))
    h = max(1, int(box["h"] / 3 * scale))
    a = previous[y:y + h, x:x + w]
    b = current[y:y + h, x:x + w]
    if a.size == 0 or a.shape != b.shape:
        return 0.0
    import numpy as np
    return float(np.mean(np.abs(a.astype("int16") - b.astype("int16"))))


def analyze_framing(video_path: Path, start: float, end: float, src_w: int,
                    src_h: int, *, min_shot: float = MIN_SHOT) -> list[dict]:
    """Segments de cadrage d'un clip : qui tient l'image et où couper.

    Décode le clip UNE fois, séquentiellement — c'est ce qui rend la mesure
    d'agitation abordable. I/O."""
    import cv2   # import paresseux : coûteux, inutile à la logique pure
    import numpy as np

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    capture = cv2.VideoCapture(str(video_path))
    crop_w, _ = crop_size(src_w, src_h)
    detections: list[list[dict]] = []
    # Agitation mesurée, indexée par IDENTITÉ du rectangle (`id(box)`) et non par
    # rang de détection : `link_tracks` range les rectangles en pistes sans dire
    # d'où chacun vient, et deux rectangles de coordonnées identiques sur la même
    # image (la cascade Haar en produit) rendraient une comparaison par valeur
    # ambiguë. Les rectangles restent tous référencés par `detections` jusqu'au
    # report, donc aucun `id` n'est recyclé entre-temps.
    activity_by_box: dict[int, float] = {}
    # Écart avec l'image précédente, image par image. Le critère de coupe est
    # relatif à la distribution du clip entier : il ne peut donc être appliqué
    # qu'une fois tout le clip lu, pas au fil de la boucle.
    diffs: list[float] = [0.0]
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        source_fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        keep_every = max(1, int(round(source_fps / FRAME_FPS)))
        detect_every = max(1, int(round(DETECT_EVERY * FRAME_FPS)))
        # Garde-fou : certains conteneurs rendent un POS_MSEC qui ne progresse
        # pas (ou reste à 0). Sans plafond de lecture, la boucle décoderait le
        # fichier entier au lieu de la fenêtre demandée.
        max_raw = int((end - start) * source_fps) + 2
        previous = None
        boxes: list[dict] = []
        index = 0
        raw = 0
        while capture.get(cv2.CAP_PROP_POS_MSEC) < end * 1000 and raw < max_raw:
            ok, frame = capture.read()
            if not ok:
                break
            raw += 1
            if raw % keep_every:
                continue
            small = cv2.cvtColor(
                cv2.resize(frame, None, fx=DETECT_SCALE, fy=DETECT_SCALE),
                cv2.COLOR_BGR2GRAY)
            is_pass = index % detect_every == 0
            if is_pass:
                found = cascade.detectMultiScale(small, 1.15, 5, minSize=(30, 30))
                boxes = [{"x": int(x / DETECT_SCALE), "y": int(y / DETECT_SCALE),
                          "w": int(w / DETECT_SCALE), "h": int(h / DETECT_SCALE)}
                         for x, y, w, h in found]
            # Chaque image reçoit ses PROPRES rectangles : entre deux détections
            # les coordonnées sont tenues, mais les objets doivent rester
            # distincts pour que le report par identité soit sans ambiguïté.
            # `detected` distingue le rectangle réellement détecté de sa copie
            # tenue — sans quoi une piste vue une seule fois paraîtrait durer
            # `detect_every` images et passerait pour quelqu'un de présent.
            frame_boxes = [dict(b, detected=is_pass) for b in boxes]
            detections.append(frame_boxes)
            if previous is not None:
                diffs.append(float(np.mean(np.abs(
                    small.astype("int16") - previous.astype("int16")))) / 255)
                for box in frame_boxes:
                    activity_by_box[id(box)] = _mouth_activity(
                        previous, small, box, DETECT_SCALE)
            previous = small
            index += 1
    finally:
        capture.release()

    cuts = detect_cuts(diffs)
    tracks = link_tracks(detections)
    # `link_tracks` range les rectangles eux-mêmes (pas des copies) dans les
    # pistes : l'identité suffit à reporter chaque mesure sur la bonne piste.
    for track in tracks:
        for frame_index, box in track["boxes"].items():
            value = activity_by_box.get(id(box))
            if value is not None:
                track["activity"][frame_index] = value
    usable = usable_tracks(tracks, src_h)
    timeline = speaker_timeline(usable, cuts, len(detections), FRAME_FPS,
                                min_shot=min_shot)
    return crop_segments(timeline, usable, crop_w, src_w, FRAME_FPS)
