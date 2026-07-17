"""Test de la sauvegarde à chaud de la base SQLite (deploy/_backup_db.py).

La base est la seule copie des données sur la tour : la sauvegarde doit être
sûre même si la webui écrit en parallèle → API backup de sqlite3 (pas une
copie de fichier brute, qui pourrait être déchirée), et produire une base
lisible avec exactement les mêmes données.
"""

import importlib.util
import sqlite3
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_backup_db", Path(__file__).resolve().parents[1] / "deploy" / "_backup_db.py")
_backup_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_backup_db)


def test_backup_copies_live_data(tmp_path):
    src = tmp_path / "platform.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('un'), ('deux')")
    conn.commit()

    dst = tmp_path / "snaps" / "platform-copy.db"
    # une connexion source reste OUVERTE pendant la sauvegarde (base "vivante")
    out = _backup_db.backup_db(src, dst)
    conn.close()

    assert out == dst
    assert dst.is_file()
    got = sqlite3.connect(dst)
    rows = got.execute("SELECT v FROM t ORDER BY id").fetchall()
    got.close()
    assert rows == [("un",), ("deux",)]
