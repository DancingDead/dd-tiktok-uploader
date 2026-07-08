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


def ytdlp_args(dest: Path, video: bool) -> list[str]:
    """Arguments yt-dlp : audio mp3 par défaut, ou vidéo ≤1080p mp4 (clips)."""
    common = ["--restrict-filenames", "--no-overwrites", "--ignore-errors",
              "-o", str(dest / "%(title)s.%(ext)s")]
    if video:
        return ["-f", "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/bv*",
                "--remux-video", "mp4", *common]
    return ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
            *common]


def download_tracks(urls: list[str], dest: Path, video: bool = False) -> int:
    """Télécharge l'audio de chaque URL en mp3 dans dest. Retourne le code yt-dlp
    (0 = tout OK ; on continue malgré les échecs individuels avec -i)."""
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", *ytdlp_args(dest, video), *urls])
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Télécharge l'audio des liens YouTube d'un fichier (un lien par ligne)."
    )
    parser.add_argument("links_file", nargs="?", default="links.txt",
                        help="fichier de liens (défaut : links.txt)")
    parser.add_argument("--dest", default="tracks", help="dossier de destination (défaut : tracks/)")
    parser.add_argument("--video", action="store_true", help="télécharge la VIDÉO (clips) au lieu de l'audio")
    args = parser.parse_args()

    links_path = Path(args.links_file)
    if not links_path.is_file():
        sys.exit(f"fichier de liens introuvable : {links_path}")

    urls = parse_links(links_path.read_text())
    if not urls:
        sys.exit(f"aucun lien dans {links_path} (un lien YouTube par ligne, # pour commenter)")

    print(f"{len(urls)} lien(s) à télécharger vers {args.dest}/")
    code = download_tracks(urls, Path(args.dest), video=args.video)
    if code != 0:
        sys.exit("certains téléchargements ont échoué (voir les messages yt-dlp ci-dessus)")
    print("Téléchargements terminés.")


if __name__ == "__main__":
    main()
