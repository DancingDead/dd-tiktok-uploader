"""trim_clip — rogne le début et la fin d'un clip du catalogue partagé.

Retire une intro ou une outro d'un clip importé depuis YouTube. La coupe est
DESTRUCTIVE : le fichier de `clips/` est réécrit. Le lien YouTube restant dans
`clip_links.txt`, un clip mal rogné se réimporte — au prix d'un téléchargement.

Réencodage et non copie de flux : les images-clés des clips du catalogue sont
très irrégulières (jusqu'à 5 s d'écart, mesuré), donc une copie laisserait
plusieurs secondes d'intro ou couperait bien trop loin.

Lancé en tâche de fond par l'interface : réencoder un clip AV1 de trois minutes
prend plusieurs minutes.

    uv run python trim_clip.py <nom-du-clip> <début> <fin> [<racine>]
"""

import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

# Durée minimale d'un clip rogné. En dessous, il n'a plus de matière
# exploitable et `usable_intervals` le rejetterait de toute façon.
MIN_TRIMMED_DUR = 1.0
# Un cran au-dessus du CRF 20 du rendu final : ce clip est un intermédiaire que
# le montage réencodera une seconde fois, lui laisser de la marge évite
# d'empiler deux générations de perte.
TRIM_CRF = 18
TRIM_PRESET = "medium"
# Tolérance sur la borne de fin. La dernière frame d'une vidéo tombe rarement
# pile sur la durée annoncée par ffprobe : poser la borne sur la fin du lecteur
# ne doit pas être un échec. Au-delà, c'est une vraie erreur de saisie.
END_TOLERANCE = 0.5


def _number(value) -> float:
    """Convertit en float en refusant les booléens et les valeurs non finies.
    `float(True)` vaut 1.0 : accepter un booléen ferait passer une faute de
    frappe pour une borne valide."""
    if isinstance(value, bool):
        raise ValueError(f"valeur invalide : {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"valeur non numérique : {value!r}")
    if not math.isfinite(number):
        raise ValueError(f"valeur non finie : {value!r}")
    return number


def coerce_bounds(start, end, duration) -> tuple[float, float]:
    """Bornes de coupe validées, en secondes. Lève ValueError si la coupe n'a
    pas de sens. Pure."""
    start, end, duration = _number(start), _number(end), _number(duration)
    # Écart minime : arrondi du lecteur, on ramène silencieusement. Écart franc :
    # erreur de saisie, on la signale plutôt que de la masquer.
    if -END_TOLERANCE <= start < 0:
        start = 0.0
    if duration < end <= duration + END_TOLERANCE:
        end = duration
    if start < 0 or end > duration:
        raise ValueError(f"bornes hors de la vidéo (durée {duration:.2f} s)")
    if end - start < MIN_TRIMMED_DUR:
        raise ValueError(
            f"un clip rogné doit durer au moins {MIN_TRIMMED_DUR} s")
    return (start, end)


def ffmpeg_trim_args(source: Path, target: Path, start: float,
                     end: float) -> list[str]:
    """Arguments FFmpeg du rognage, sans le binaire. Pure."""
    return [
        "-y", "-loglevel", "error",
        # Seek d'entrée : placé après -i, ffmpeg décoderait toute la vidéo
        # depuis le début avant d'atteindre la coupe.
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source),
        "-c:v", "libx264", "-crf", str(TRIM_CRF), "-preset", TRIM_PRESET,
        "-pix_fmt", "yuv420p",
        # L'audio d'un clip n'est jamais lu par beatsync, mais détruire une
        # donnée dont on n'a pas besoin n'est pas une raison de la détruire.
        # `copy` est un no-op quand il n'y a pas de piste.
        "-c:a", "copy", "-movflags", "+faststart",
        # Mêmes flags que le reste du projet : sans eux, l'encodeur date le
        # fichier et deux encodages identiques diffèrent octet à octet.
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
        str(target),
    ]
