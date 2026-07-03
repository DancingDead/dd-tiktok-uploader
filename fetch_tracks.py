"""fetch_tracks — télécharge l'audio des liens YouTube listés dans un fichier.

Un lien par ligne (vidéo ou playlist), lignes vides et commentaires `#` ignorés.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def parse_links(text: str) -> list[str]:
    """Liens du fichier, dans l'ordre, sans doublons ni commentaires. Logique pure."""
    links: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line not in seen:
            seen.add(line)
            links.append(line)
    return links


def download_tracks(urls: list[str], dest: Path) -> int:
    """Télécharge l'audio de chaque URL en mp3 dans dest. Retourne le code yt-dlp
    (0 = tout OK ; on continue malgré les échecs individuels avec -i)."""
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable, "-m", "yt_dlp",
            "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
            "--restrict-filenames",   # noms de fichiers sans espaces/accents : plus simple en CLI
            "--no-overwrites",        # relancer le script ne retélécharge pas l'existant
            "--ignore-errors",        # un lien mort ne bloque pas les suivants
            "-o", str(dest / "%(title)s.%(ext)s"),
            *urls,
        ]
    )
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Télécharge l'audio des liens YouTube d'un fichier (un lien par ligne)."
    )
    parser.add_argument("links_file", nargs="?", default="links.txt",
                        help="fichier de liens (défaut : links.txt)")
    parser.add_argument("--dest", default="tracks", help="dossier de destination (défaut : tracks/)")
    args = parser.parse_args()

    links_path = Path(args.links_file)
    if not links_path.is_file():
        sys.exit(f"fichier de liens introuvable : {links_path}")

    urls = parse_links(links_path.read_text())
    if not urls:
        sys.exit(f"aucun lien dans {links_path} (un lien YouTube par ligne, # pour commenter)")

    print(f"{len(urls)} lien(s) à télécharger vers {args.dest}/")
    code = download_tracks(urls, Path(args.dest))
    if code != 0:
        sys.exit("certains téléchargements ont échoué (voir les messages yt-dlp ci-dessus)")
    print("Téléchargements terminés.")


if __name__ == "__main__":
    main()
