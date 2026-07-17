"""Sauvegarde à chaud (online) de la base SQLite.

Utilise l'API `sqlite3.Connection.backup` : cohérente même si la webui écrit
en parallèle (contrairement à une copie de fichier brute, qui peut être
déchirée si une écriture est en cours). Appelé par deploy/backup.ps1.

    python deploy/_backup_db.py <source.db> <destination.db>
"""

import sqlite3
import sys
from pathlib import Path


def backup_db(src, dst) -> Path:
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return dst


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python _backup_db.py <source.db> <destination.db>", file=sys.stderr)
        raise SystemExit(2)
    print(backup_db(sys.argv[1], sys.argv[2]))
