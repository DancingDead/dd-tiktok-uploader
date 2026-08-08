import pytest

from speaker import speaker_timeline


def piste(id_, activity):
    """`activity` : dict index d'image -> agitation. Les rectangles ne servent
    pas ici, mais la structure doit être complète."""
    return {"id": id_, "boxes": {i: {"x": id_ * 500, "y": 0, "w": 200, "h": 200}
                                 for i in activity},
            "activity": dict(activity)}


FPS = 10.0


def test_une_seule_piste_tient_tout_le_clip():
    a = piste(0, {i: 5.0 for i in range(100)})
    tl = speaker_timeline([a], cuts=set(), n_frames=100, fps=FPS)
    assert tl == [{"start": 0.0, "end": 10.0, "track_id": 0}]


def test_aucune_piste_donne_un_segment_centre():
    tl = speaker_timeline([], cuts=set(), n_frames=100, fps=FPS)
    assert tl == [{"start": 0.0, "end": 10.0, "track_id": None}]


def test_bascule_quand_l_autre_domine():
    """Le premier parle 5 s, le second les 5 s suivantes."""
    a = piste(0, {i: (9.0 if i < 50 else 0.5) for i in range(100)})
    b = piste(1, {i: (0.5 if i < 50 else 9.0) for i in range(100)})
    tl = speaker_timeline([a, b], cuts=set(), n_frames=100, fps=FPS)
    assert [s["track_id"] for s in tl] == [0, 1]
    assert tl[0]["end"] == pytest.approx(tl[1]["start"])
    assert 4.0 < tl[1]["start"] < 6.5


def test_pas_de_bascule_sous_la_marge():
    """1,2 fois plus agité ne suffit pas : deux personnes qui se coupent la
    parole feraient osciller le cadre."""
    a = piste(0, {i: (9.0 if i < 50 else 5.0) for i in range(100)})
    b = piste(1, {i: (5.0 if i < 50 else 6.0) for i in range(100)})
    tl = speaker_timeline([a, b], cuts=set(), n_frames=100, fps=FPS)
    assert [s["track_id"] for s in tl] == [0]


def test_min_shot_empeche_le_clignotement():
    """La domination alterne toutes les 1,0 s (plus lentement que la fenêtre
    glissante de 0,6 s, pour que les bascules aient réellement lieu) ; avec
    min_shot à 1,2 s le cadre ne peut pas changer plus vite qu'une fois par
    1,2 s. Le test exige aussi plusieurs segments : sinon `durees[:-1]` est
    vide et la garantie ne serait pas vérifiée."""
    a = piste(0, {i: (9.0 if (i // 10) % 2 == 0 else 0.0) for i in range(120)})
    b = piste(1, {i: (0.0 if (i // 10) % 2 == 0 else 9.0) for i in range(120)})
    tl = speaker_timeline([a, b], cuts=set(), n_frames=120, fps=FPS, min_shot=1.2)
    assert len(tl) >= 2
    durees = [s["end"] - s["start"] for s in tl]
    assert all(d >= 1.2 - 1e-6 for d in durees[:-1])


def test_un_cadrage_initial_a_l_aveugle_ne_verrouille_pas_le_plan():
    """Personne ne parle sur les premieres images : le choix initial est
    arbitraire (egalite a zero, on prend le plus petit id). Il ne doit pas
    bloquer le plancher contre celui qui prend la parole ensuite.

    La fenetre de mesure etant centree, elle voit 0,3 s dans le futur : pour
    que le choix initial soit vraiment aveugle, la parole doit commencer bien
    au-dela de cette demi-fenetre."""
    a = piste(0, {i: 0.0 for i in range(100)})
    b = piste(1, {i: (0.0 if i < 10 else 9.0) for i in range(100)})
    tl = speaker_timeline([a, b], cuts=set(), n_frames=100, fps=FPS, min_shot=1.2)
    assert tl[-1]["track_id"] == 1
    assert tl[0]["end"] < 1.2      # la bascule n'a pas attendu le plancher


def test_une_rafale_de_coupes_ne_produit_pas_de_plans_d_une_image():
    """Toutes les images marquees comme coupe : sans plancher court, on
    obtiendrait des plans de 0,1 s."""
    a = piste(0, {i: (9.0 if (i // 10) % 2 == 0 else 0.0) for i in range(120)})
    b = piste(1, {i: (0.0 if (i // 10) % 2 == 0 else 9.0) for i in range(120)})
    tl = speaker_timeline([a, b], cuts=set(range(120)), n_frames=120, fps=FPS,
                          min_shot=1.2)
    assert len(tl) >= 2
    assert all(s["end"] - s["start"] >= 0.4 - 1e-6 for s in tl[:-1])


def test_une_coupe_de_la_source_autorise_une_bascule_plus_tot():
    """Sans coupe, min_shot repousse le changement à 1,2 s. La coupe le rend
    invisible, donc on l'autorise avant. La fenêtre glissante décale forcément
    la détection de quelques images : le test compare les deux cas plutôt que
    d'exiger un instant précis."""
    a = piste(0, {i: (9.0 if i < 5 else 0.0) for i in range(40)})
    b = piste(1, {i: (0.0 if i < 5 else 9.0) for i in range(40)})
    sans = speaker_timeline([a, b], cuts=set(), n_frames=40, fps=FPS, min_shot=1.2)
    avec = speaker_timeline([a, b], cuts=set(range(5, 12)), n_frames=40, fps=FPS,
                            min_shot=1.2)
    assert [s["track_id"] for s in sans] == [0, 1]
    assert [s["track_id"] for s in avec] == [0, 1]
    assert avec[1]["start"] < sans[1]["start"]


def test_le_silence_conserve_le_cadrage_courant():
    """Personne ne parle sur la seconde moitié : on tient, on ne recentre pas."""
    a = piste(0, {i: (9.0 if i < 50 else 0.0) for i in range(100)})
    b = piste(1, {i: 0.0 for i in range(100)})
    tl = speaker_timeline([a, b], cuts=set(), n_frames=100, fps=FPS)
    assert [s["track_id"] for s in tl] == [0]


def test_la_timeline_est_contigue_et_couvre_tout_le_clip():
    a = piste(0, {i: (9.0 if i < 50 else 0.5) for i in range(100)})
    b = piste(1, {i: (0.5 if i < 50 else 9.0) for i in range(100)})
    tl = speaker_timeline([a, b], cuts=set(), n_frames=100, fps=FPS)
    assert tl[0]["start"] == 0.0
    assert tl[-1]["end"] == pytest.approx(10.0)
    for gauche, droite in zip(tl, tl[1:]):
        assert gauche["end"] == pytest.approx(droite["start"])


def test_deterministe():
    a = piste(0, {i: (9.0 if i < 50 else 0.5) for i in range(100)})
    b = piste(1, {i: (0.5 if i < 50 else 9.0) for i in range(100)})
    args = ([a, b], set(), 100, FPS)
    assert speaker_timeline(*args) == speaker_timeline(*args)


def test_clip_vide():
    assert speaker_timeline([], cuts=set(), n_frames=0, fps=FPS) == []
