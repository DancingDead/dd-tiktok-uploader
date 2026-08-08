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


def crop_expr(track: list[float], sample_fps: float, crop_w: int,
              src_w: int) -> str:
    """Compile la trajectoire en expression FFmpeg pour `crop=x=…`, en
    interpolant linéairement entre les points de la trajectoire. Une trajectoire
    immobile rend un simple nombre — ce qui évite de demander une réévaluation
    par frame (`eval=frame`, quand la ffmpeg installée connaît encore l'option,
    voir `crop_supports_eval`) pour une constante. Pure."""
    centre = _even(max(0, (src_w - crop_w) / 2))
    if not track:
        return str(centre)

    xs = [_even(min(max(0.0, c - crop_w / 2), max(0, src_w - crop_w)))
          for c in track]
    # Un point par seconde suffit : la zone morte a déjà supprimé tout ce qui
    # bouge plus vite, et un palier par frame ferait exploser l'expression.
    step = max(1, int(round(sample_fps)))
    keyed = [(i / sample_fps, xs[i]) for i in range(0, len(xs), step)]
    # Paliers consécutifs identiques fusionnés : c'est le cas courant (plan fixe).
    merged: list[tuple[float, int]] = []
    for time, x in keyed:
        if not merged or merged[-1][1] != x:
            merged.append((time, x))
    if len(merged) > MAX_STEPS:
        keep = max(1, len(merged) // MAX_STEPS + 1)
        merged = merged[::keep]
    if len(merged) == 1:
        return str(merged[0][1])

    # Emboîtement en partant de la fin : chaque segment interpole jusqu'au point
    # suivant ; après le dernier point, le cadre tient sa valeur.
    expr = str(merged[-1][1])
    for (time, x), (next_time, next_x) in zip(reversed(merged[:-1]),
                                              reversed(merged[1:])):
        expr = (f"if(lt(t,{next_time:g}),"
                f"{_ramp(time, x, next_time, next_x)},{expr})")
    return expr
