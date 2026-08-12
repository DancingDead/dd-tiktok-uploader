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


def largeur(caption, tmp_path, taille=90):
    """Largeur en pixels du texte rendu. Sert à prouver qu'un caractère est bien
    DESSINÉ : un glyphe manquant ne se voit pas autrement, le texte restant
    parfaitement centré et lisible."""
    entry = {"timeline_start": 0, "duration": 1.0, "effects": [], "layout": "crop",
             "focus_x": 0.5, "speed": 1.0, "caption": caption}
    config = dict(beatsync.DEFAULT_CONFIG,
                  subtitles={**beatsync.DEFAULT_CONFIG["subtitles"],
                             "enabled": True, "size": taille, "y": 0.5})
    out = tmp_path / "l.png"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=gray:s=1400x400:d=1", "-vf",
                        beatsync._caption_filter(entry, config),
                        "-frames:v", "1", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    img = cv2.imread(str(out), cv2.IMREAD_GRAYSCALE)
    cols = np.where(img.std(axis=0) > 5)[0]
    return 0 if len(cols) == 0 else cols[-1] - cols[0]


def test_apostrophe_ne_casse_pas_les_options(tmp_path):
    """Le bug d'origine. Avec un seul antislash, le texte partait à y≈0."""
    assert centre(rendu("C'est maintenant", tmp_path))


def test_apostrophe_est_reellement_dessinee(tmp_path):
    """Second piège, plus sournois que le premier : à deux antislashs les
    options redevenaient saines et le texte bien centré, mais l'apostrophe
    n'était plus dessinée — « world's » sortait « worlds ». Le test qui ne
    regardait que la position était vert. On compare donc les largeurs."""
    avec = largeur("world's", tmp_path)
    sans = largeur("worlds", tmp_path)
    assert avec > sans + 8, f"apostrophe absente du rendu ({avec} vs {sans})"


def test_apostrophe_ne_dessine_pas_un_antislash_en_plus(tmp_path):
    """L'excès inverse : un antislash de trop et c'est « world\\'s » qui
    s'affiche. Le glyphe surnuméraire est plus large que l'apostrophe seule."""
    juste = largeur("world's", tmp_path)
    sans = largeur("worlds", tmp_path)
    assert juste - sans < 30, f"un caractere de trop est dessine ({juste - sans} px)"


def test_deux_points_et_virgule_sont_dessines(tmp_path):
    assert largeur("a:b", tmp_path) > largeur("ab", tmp_path) + 5
    assert largeur("a,b", tmp_path) > largeur("ab", tmp_path) + 5


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


# --- Retour a la ligne automatique -------------------------------------------


def test_wrap_caption_coupe_aux_espaces():
    from beatsync import wrap_caption
    assert wrap_caption("un deux trois quatre", 10) == "un deux\ntrois\nquatre"


def test_wrap_caption_preserve_les_sauts_voulus():
    """L'accroche du mode `llm_unique` est deja en deux lignes : le repli ne doit
    pas les fusionner, seulement replier celles qui debordent."""
    from beatsync import wrap_caption
    assert wrap_caption("ligne une\nligne deux", 40) == "ligne une\nligne deux"
    assert wrap_caption("aaa bbb\nccc ddd", 4) == "aaa\nbbb\nccc\nddd"


def test_wrap_caption_laisse_un_mot_trop_long_seul():
    """Mieux vaut un mot rogne qu'un mot coupe en deux : on ne casse jamais un
    mot, on le pose seul sur sa ligne."""
    from beatsync import wrap_caption
    assert wrap_caption("court anticonstitutionnellement court", 8) == (
        "court\nanticonstitutionnellement\ncourt")


def test_wrap_caption_texte_vide():
    from beatsync import wrap_caption
    assert wrap_caption("", 10) == ""


def test_la_punchline_qui_debordait_tient_maintenant_dans_le_cadre(tmp_path):
    """Cas reel : cette accroche produite par Gemma sortait du cadre a gauche ET
    a droite (le « Y » de Your et le « T » de That etaient rognes), parce que le
    texte est centre sur son point d'ancrage et qu'aucun repli n'existait."""
    cap = ("Your cold coffee sits while your dreams remain untouched.\n"
           "That dusty laptop holds the life you refuse to build.")
    entry = {"timeline_start": 0, "duration": 1.0, "effects": [], "layout": "crop",
             "focus_x": 0.5, "speed": 1.0, "caption": cap}
    config = dict(beatsync.DEFAULT_CONFIG, width=1080, height=1920,
                  subtitles={**beatsync.DEFAULT_CONFIG["subtitles"],
                             "enabled": True, "size": 64, "y": 0.5})
    out = tmp_path / "large.png"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=gray:s=1080x1920:d=1", "-vf",
                        beatsync._caption_filter(entry, config),
                        "-frames:v", "1", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    img = cv2.imread(str(out), cv2.IMREAD_GRAYSCALE)
    cols = np.where(img.std(axis=0) > 5)[0]
    assert len(cols) > 0, "aucun texte rendu"
    # Rien ne doit toucher les bords : un texte rogne colle a la colonne 0 ou 1079.
    assert cols[0] > 2 and cols[-1] < 1077, f"texte rogne (colonnes {cols[0]}-{cols[-1]})"
