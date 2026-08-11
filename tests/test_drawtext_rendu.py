"""Le drawtext des punchlines, vérifié EN RENDANT une image avec FFmpeg.

Ces tests existent parce qu'un test de chaîne a laissé passer un bug majeur :
`_drawtext_escape` échappait l'apostrophe avec UN antislash, ce qui est faux —
le filtergraph applique deux niveaux d'unescape, si bien que l'apostrophe
refermait l'option `text=` et que TOUTES les options suivantes (taille, couleur,
position) étaient avalées dans le texte. La punchline se dessinait minuscule et
noire dans le coin, donc invisible. Le test de l'époque comparait la sortie
d'`_drawtext_escape` à une constante tout aussi fausse : il était vert et le
rendu cassé. En français, l'apostrophe est partout — le mode punchlines était
inutilisable sans que rien ne le signale.

On ne teste donc plus la chaîne échappée, on regarde le pixel.
"""

import subprocess

import numpy as np
import pytest

import beatsync

cv2 = pytest.importorskip("cv2")


def _ffmpeg_dispo() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _ffmpeg_dispo(), reason="FFmpeg absent")

H = 960


def rendu(caption, tmp_path, taille=70):
    """Rend la caption sur un fond uni et retourne les lignes de pixels qui
    portent du texte. Le fond est uni : tout écart-type non nul est du texte."""
    entry = {"timeline_start": 0, "duration": 1.0, "effects": [], "layout": "crop",
             "focus_x": 0.5, "speed": 1.0, "caption": caption}
    config = dict(beatsync.DEFAULT_CONFIG,
                  subtitles={**beatsync.DEFAULT_CONFIG["subtitles"],
                             "enabled": True, "size": taille, "y": 0.5})
    filtre = beatsync._caption_filter(entry, config)
    assert filtre is not None
    out = tmp_path / "f.png"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=gray:s=720x{H}:d=1", "-vf", filtre,
                        "-frames:v", "1", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    img = cv2.imread(str(out), cv2.IMREAD_GRAYSCALE)
    return np.where(img.std(axis=1) > 5)[0]


def centre(lignes):
    """Le texte est-il posé au point d'ancrage (y=0.5) et non dans le coin ?
    Un texte collé en haut signe des options avalées par le parseur."""
    return len(lignes) > 0 and 0.35 * H < lignes[0] < 0.60 * H


def test_apostrophe_ne_casse_pas_les_options(tmp_path):
    """Le bug d'origine. Avec un seul antislash, le texte partait à y≈0."""
    assert centre(rendu("C'est maintenant", tmp_path))


def test_deux_points_ne_cassent_pas_les_options(tmp_path):
    assert centre(rendu("Motivation: maintenant", tmp_path))


def test_pourcent_ne_vide_pas_le_texte(tmp_path):
    """Sans `expansion=none`, le `%` déclenche l'expansion de drawtext et le
    texte rendu était VIDE — aucune erreur, aucune trace."""
    assert centre(rendu("100% ou rien", tmp_path))


def test_virgule_et_crochets(tmp_path):
    assert centre(rendu("Bouge, ou dors [vraiment]", tmp_path))


def test_saut_de_ligne_rend_bien_deux_lignes(tmp_path):
    """L'accroche du mode `llm_unique` tient en deux lignes. La séquence `\\n`
    fabriquée par l'ancien code se dessinait comme un « n » littéral."""
    lignes = rendu("A quiet room fails\nTo block the world's noise", tmp_path)
    assert centre(lignes)
    blocs = 1 + int((np.diff(lignes) > 10).sum())
    assert blocs == 2, f"{blocs} bloc(s) de texte au lieu de 2"
