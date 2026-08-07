from pathlib import PureWindowsPath

from clipper import ASS_HIGHLIGHT, ass_time, build_ass, ffmpeg_path


def w(text, start, end):
    return {"word": text, "start": start, "end": end}


WORDS = [w("Le", 10.0, 10.3), w("hardstyle", 10.3, 10.9), w("c'est", 10.9, 11.2),
         w("violent", 11.2, 11.8), w("et", 11.8, 12.0), w("j'assume", 12.0, 12.6)]


def test_ass_time_format():
    assert ass_time(0.0) == "0:00:00.00"
    assert ass_time(75.5) == "0:01:15.50"
    assert ass_time(3661.25) == "1:01:01.25"


def test_ass_contient_un_entete_valide():
    out = build_ass(WORDS, 10.0, 12.6)
    assert "[Script Info]" in out
    assert "PlayResX: 1080" in out and "PlayResY: 1920" in out
    assert "[V4+ Styles]" in out and "[Events]" in out


def test_un_evenement_par_mot():
    out = build_ass(WORDS, 10.0, 12.6)
    assert out.count("\nDialogue:") == len(WORDS)


def test_les_temps_sont_rebases_sur_le_debut_du_clip():
    """Le clip commence à 10 s dans la source, mais à 0 dans le fichier rendu."""
    out = build_ass(WORDS, 10.0, 12.6)
    assert "Dialogue: 0,0:00:00.00," in out


def test_le_mot_courant_est_surligne_les_autres_non():
    out = build_ass(WORDS, 10.0, 12.6)
    first = [ln for ln in out.splitlines() if ln.startswith("Dialogue:")][0]
    assert first.count(ASS_HIGHLIGHT) == 1        # un seul mot en rouge
    assert "hardstyle" in first                   # mais la ligne entière s'affiche


def test_la_ligne_ne_depasse_pas_quatre_mots():
    out = build_ass(WORDS, 10.0, 12.6)
    fifth = [ln for ln in out.splitlines() if ln.startswith("Dialogue:")][4]
    assert "Le" not in fifth                      # nouvelle ligne, mots 5 et 6


def test_les_mots_hors_du_clip_sont_ignores():
    words = WORDS + [w("apres", 30.0, 30.5)]
    out = build_ass(words, 10.0, 12.6)
    assert "apres" not in out


def test_le_texte_est_echappe():
    """Accolades et antislash pilotent les balises ASS : non échappés, le texte
    de l'orateur devient une commande de rendu."""
    out = build_ass([w("{gras}", 0.0, 1.0)], 0.0, 1.0)
    assert "{gras}" not in out
    assert r"\{gras\}" in out or "(gras)" in out


def test_sans_mot_dans_la_fenetre_le_fichier_reste_valide():
    out = build_ass([], 0.0, 30.0)
    assert "[Events]" in out
    assert "\nDialogue:" not in out


def test_ffmpeg_path_echappe_les_chemins_windows():
    """Sur la tour, `C:\\data\\x.ass` non échappé casse la chaîne de filtre :
    ffmpeg lit `:` comme un séparateur d'argument."""
    out = ffmpeg_path(PureWindowsPath(r"C:\data\clipper\x.ass"))
    assert ":" not in out.replace("\\:", "")
    assert "\\\\" not in out          # séparateurs normalisés en /


def test_ffmpeg_path_laisse_un_chemin_posix_lisible():
    assert ffmpeg_path("/tmp/a/x.ass") == "/tmp/a/x.ass"


def test_ffmpeg_path_neutralise_l_apostrophe():
    """Le filtre s'écrit subtitles='<chemin>' : une apostrophe dans le chemin
    refermerait la chaîne et la fin du chemin passerait pour des options."""
    assert ffmpeg_path("/home/o'brien/x.ass") == "/home/o'\\''brien/x.ass"
