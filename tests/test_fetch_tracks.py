"""Tests de parse_links — la partie pure de fetch_tracks."""

from fetch_tracks import parse_links


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
