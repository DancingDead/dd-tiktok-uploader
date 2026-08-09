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


def _dialogue_times(line: str) -> tuple[str, str]:
    fields = line.split(",")
    return fields[1], fields[2]


def test_pas_de_trou_entre_deux_mots_d_une_meme_ligne():
    """Borner chaque Dialogue sur la fin du mot éteint le sous-titre pendant
    chaque respiration : sur du français parlé, la ligne clignote en continu."""
    words = [w("Le", 0.0, 0.3), w("hardstyle", 0.8, 1.4),
             w("c'est", 2.0, 2.3), w("violent", 2.4, 3.0)]
    out = build_ass(words, 0.0, 3.0)
    lignes = [ln for ln in out.splitlines() if ln.startswith("Dialogue:")]
    for courant, suivant in zip(lignes, lignes[1:]):
        assert _dialogue_times(courant)[1] == _dialogue_times(suivant)[0]


def test_pas_de_trou_entre_deux_groupes():
    """Le dernier mot d'un groupe de 4 s'étend jusqu'au premier mot du groupe
    suivant, comme à l'intérieur d'un groupe : sinon la ligne s'éteint entre
    deux groupes, exactement le même clignotement que le test ci-dessus couvre
    à l'intérieur d'une ligne. Prolonger jusqu'au `start` du mot suivant ne
    crée aucun chevauchement : l'événement précédent finit où le suivant
    commence."""
    words = [w("Le", 0.0, 0.3), w("hardstyle", 0.3, 0.9), w("c'est", 0.9, 1.2),
              w("violent", 1.2, 1.8), w("et", 2.5, 2.7), w("j'assume", 2.7, 3.2)]
    out = build_ass(words, 0.0, 3.2)
    lignes = [ln for ln in out.splitlines() if ln.startswith("Dialogue:")]
    assert len(lignes) == 6
    # Le 4e mot (fin de groupe 1) doit tenir jusqu'au 5e (début groupe 2).
    assert _dialogue_times(lignes[3])[1] == _dialogue_times(lignes[4])[0]
    assert _dialogue_times(lignes[3])[1] == ass_time(2.5)
    # Aucun Dialogue ne chevauche le suivant, tous groupes confondus.
    for courant, suivant in zip(lignes, lignes[1:]):
        assert _dialogue_times(courant)[1] == _dialogue_times(suivant)[0]


def test_le_dernier_mot_du_clip_tient_jusqu_a_sa_propre_fin():
    """Seul le tout dernier mot du clip n'a pas de mot suivant à qui céder :
    il garde sa propre fin, sans quoi la ligne resterait affichée sur du vide."""
    out = build_ass([w("un", 0.0, 0.2), w("deux", 1.0, 1.5)], 0.0, 3.0)
    dernier = [ln for ln in out.splitlines() if ln.startswith("Dialogue:")][-1]
    assert _dialogue_times(dernier)[1] == ass_time(1.5)


def test_le_style_nomme_la_police_embarquee():
    """Impact n'est pas installable partout : libass lui substituerait
    silencieusement une sans-serif. Anton est le substitut OFL déjà embarqué
    par beatsync pour le nom logique « impact »."""
    from clipper import ASS_FONT
    out = build_ass(WORDS, 10.0, 12.6)
    assert f"Style: DD,{ASS_FONT}," in out
    assert "Impact" not in out


def test_la_police_embarquee_existe_bien():
    from beatsync import FONTS_DIR
    assert (FONTS_DIR / "Anton-Regular.ttf").is_file()
