"""Tests de parse_links — la partie pure de fetch_tracks."""

from pathlib import Path

from fetch_tracks import parse_links, ytdlp_args


def test_one_link_per_line():
    text = "https://youtu.be/aaa\nhttps://youtu.be/bbb\n"
    assert parse_links(text) == ["https://youtu.be/aaa", "https://youtu.be/bbb"]


def test_ignores_blank_lines_and_comments():
    text = "# morceaux de la compil\n\n  \nhttps://youtu.be/aaa\n# pause\nhttps://youtu.be/bbb"
    assert parse_links(text) == ["https://youtu.be/aaa", "https://youtu.be/bbb"]


def test_strips_whitespace():
    assert parse_links("  https://youtu.be/aaa  \n") == ["https://youtu.be/aaa"]


def test_dedupes_preserving_order():
    text = "https://youtu.be/bbb\nhttps://youtu.be/aaa\nhttps://youtu.be/bbb\n"
    assert parse_links(text) == ["https://youtu.be/bbb", "https://youtu.be/aaa"]


def test_empty_text_gives_empty_list():
    assert parse_links("") == []
    assert parse_links("# rien que des commentaires\n\n") == []


def test_ytdlp_args_audio_default():
    args = ytdlp_args(Path("tracks"), video=False)
    assert "--extract-audio" in args
    assert "mp3" in args


def test_ytdlp_args_video_mode():
    args = ytdlp_args(Path("clips"), video=True)
    assert "--extract-audio" not in args
    assert any("bv*[height<=1080]" in a for a in args)
    assert "--remux-video" in args and "mp4" in args
    # chaque alternative du sélecteur -f doit être plafonnée à 1080p
    selector = args[args.index("-f") + 1]
    for alternative in selector.split("/"):
        assert "[height<=1080]" in alternative, alternative
    # Le catalogue clips/ de beatsync n'a que faire du son : le défaut ne doit
    # pas changer, sinon tout le catalogue serait à retélécharger.
    assert "+ba" not in selector
    assert "--merge-output-format" not in args


def test_ytdlp_args_refuse_les_playlists_implicites_dans_les_trois_modes():
    """Un lien copié depuis le lecteur YouTube porte presque toujours un
    `&list=` (un mix radio `RD…` quand on clique un morceau depuis une
    recherche) : sans `--no-playlist`, importer UN son déverse des dizaines de
    fichiers. La garde vaut pour les trois modes, pas seulement l'audio."""
    for kwargs in ({"video": False},
                   {"video": True},
                   {"video": True, "with_audio": True}):
        assert "--no-playlist" in ytdlp_args(Path("dest"), **kwargs), kwargs


def test_ytdlp_args_video_avec_audio_muxe_une_piste_son():
    """Sans +ba, yt-dlp rend le meilleur flux VIDÉO SEULE et la source du
    clipper arrive muette — donc condamnée avant la transcription."""
    args = ytdlp_args(Path("inbox"), video=True, with_audio=True)
    selector = args[args.index("-f") + 1]
    assert "+ba" in selector
    assert args[args.index("--merge-output-format") + 1] == "mp4"
