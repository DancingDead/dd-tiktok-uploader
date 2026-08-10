"""trim_clip — rogne le début et la fin d'un clip du catalogue partagé.

Retire une intro ou une outro d'un clip importé depuis YouTube. La coupe est
DESTRUCTIVE : le fichier de `clips/` est réécrit. Le lien YouTube restant dans
`clip_links.txt`, un clip mal rogné se réimporte — au prix d'un téléchargement.

Réencodage et non copie de flux : les images-clés des clips du catalogue sont
très irrégulières (jusqu'à 5 s d'écart, mesuré), donc une copie laisserait
plusieurs secondes d'intro ou couperait bien trop loin.

Lancé en tâche de fond par l'interface : réencoder un clip AV1 de trois minutes
prend plusieurs minutes.

    uv run python trim_clip.py <nom-du-clip> <début> <fin> [<racine>]
"""

import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent

# Durée minimale d'un clip rogné. En dessous, il n'a plus de matière
# exploitable et `usable_intervals` le rejetterait de toute façon.
MIN_TRIMMED_DUR = 1.0
# Un cran au-dessus du CRF 20 du rendu final : ce clip est un intermédiaire que
# le montage réencodera une seconde fois, lui laisser de la marge évite
# d'empiler deux générations de perte.
TRIM_CRF = 18
TRIM_PRESET = "medium"
# Tolérance sur la borne de fin. La dernière frame d'une vidéo tombe rarement
# pile sur la durée annoncée par ffprobe : poser la borne sur la fin du lecteur
# ne doit pas être un échec. Au-delà, c'est une vraie erreur de saisie.
END_TOLERANCE = 0.5


def _number(value) -> float:
    """Convertit en float en refusant les booléens et les valeurs non finies.
    `float(True)` vaut 1.0 : accepter un booléen ferait passer une faute de
    frappe pour une borne valide."""
    if isinstance(value, bool):
        raise ValueError(f"valeur invalide : {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"valeur non numérique : {value!r}")
    if not math.isfinite(number):
        raise ValueError(f"valeur non finie : {value!r}")
    return number


def coerce_bounds(start, end, duration) -> tuple[float, float]:
    """Bornes de coupe validées, en secondes. Lève ValueError si la coupe n'a
    pas de sens. Pure."""
    start, end, duration = _number(start), _number(end), _number(duration)
    # Écart minime : arrondi du lecteur, on ramène silencieusement. Écart franc :
    # erreur de saisie, on la signale plutôt que de la masquer.
    if -END_TOLERANCE <= start < 0:
        start = 0.0
    if duration < end <= duration + END_TOLERANCE:
        end = duration
    if start < 0 or end > duration:
        raise ValueError(f"bornes hors de la vidéo (durée {duration:.2f} s)")
    if end - start < MIN_TRIMMED_DUR:
        raise ValueError(
            f"un clip rogné doit durer au moins {MIN_TRIMMED_DUR} s")
    return (start, end)


def ffmpeg_trim_args(source: Path, target: Path, start: float,
                     end: float) -> list[str]:
    """Arguments FFmpeg du rognage, sans le binaire. Pure."""
    return [
        "-y", "-loglevel", "error",
        # Seek d'entrée : placé après -i, ffmpeg décoderait toute la vidéo
        # depuis le début avant d'atteindre la coupe.
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source),
        "-c:v", "libx264", "-crf", str(TRIM_CRF), "-preset", TRIM_PRESET,
        "-pix_fmt", "yuv420p",
        # L'audio d'un clip n'est jamais lu par beatsync, mais détruire une
        # donnée dont on n'a pas besoin n'est pas une raison de la détruire.
        # `copy` est un no-op quand il n'y a pas de piste.
        "-c:a", "copy", "-movflags", "+faststart",
        # Même invariant que le reste du projet (sans eux, l'encodeur date le
        # fichier et deux encodages identiques diffèrent octet à octet), mais
        # pas la même écriture : beatsync pose la forme globale
        # `-bitexact -map_metadata -1`, ici les trois formes séparées
        # (conteneur, vidéo, audio) et pas de `-map_metadata -1`. Les
        # métadonnées de la source sont volontairement conservées : ce fichier
        # reste un clip du catalogue, pas une sortie finale.
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
        str(target),
    ]


# Le temporaire vit dans un SOUS-DOSSIER de clips/ : même volume, donc le
# remplacement final est un simple rename atomique. Un sous-dossier plutôt
# qu'un fichier voisin parce que `load_clips` et `/api/state` parcourent
# `clips/` et prendraient un `xxx.trim.mp4` oublié pour un vrai clip.
TRIM_DIR_NAME = ".trim"
# Âge au-delà duquel un temporaire de `.trim/` est considéré comme mort et
# supprimé. 24 h : très au-dessus du rognage le plus long (quelques minutes),
# donc aucun risque d'effacer le temporaire d'un rognage en cours, y compris
# celui d'un autre process.
TRIM_MAX_AGE = 24 * 3600


def purge_temporaires(temp_dir: Path, max_age: float = TRIM_MAX_AGE,
                      now: float | None = None, log=print) -> list[str]:
    """Supprime les temporaires morts de `.trim/` et rend leurs noms. I/O.

    Chaque `kill -9`, coupure de courant ou `replace` refusé laisse dans ce
    dossier un mp4 partiel de plusieurs centaines de mégaoctets. Or `.trim/`
    est invisible par construction : ni l'interface, ni `load_clips`, ni la
    colonne Taille ne le montrent — personne n'ira l'inspecter, et le premier
    symptôme serait une génération qui échoue sur disque plein."""
    now = time.time() if now is None else now
    purges = []
    for entry in temp_dir.glob("*"):
        try:
            if not entry.is_file() or now - entry.stat().st_mtime <= max_age:
                continue
            entry.unlink()
        except OSError as exc:
            # Le ménage n'est pas la mission : un temporaire verrouillé
            # (antivirus sous Windows) ne doit pas empêcher le rognage.
            log(f"(temporaire {entry.name} non supprimé : {exc})")
            continue
        purges.append(entry.name)
    if purges:
        log(f"Nettoyage de {TRIM_DIR_NAME}/ : {len(purges)} temporaire(s) mort(s) supprimé(s)")
    return purges


def probe_duration(path: Path) -> float:
    """Durée du clip en secondes. I/O."""
    # Pas de check=True : même motif que pour ffmpeg plus bas — un
    # CalledProcessError n'expose que le code retour, jamais le stderr
    # d'ffprobe, qui est le seul indice utile sur un fichier corrompu.
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe a échoué sur {path.name} :\n{result.stderr}")
    out = result.stdout.strip()
    try:
        return float(out)
    except ValueError:
        # Conteneur sans durée déclarée (ffprobe répond alors "N/A") : un
        # message clair vaut mieux que la ValueError brute de float().
        raise RuntimeError(f"durée illisible pour {path.name} : {out!r}")


def trim_clip(path: Path, start, end, log=print) -> None:
    """Rogne le clip en place. Le fichier d'origine n'est remplacé qu'après un
    code retour FFmpeg nul ET une vérification de la durée produite : une
    interruption, comme une source illisible, laisse le clip intact. I/O."""
    start, end = coerce_bounds(start, end, probe_duration(path))
    temp_dir = path.parent / TRIM_DIR_NAME
    temp_dir.mkdir(exist_ok=True)
    purge_temporaires(temp_dir, log=log)
    # Nom unique par exécution (PID), pas juste `path.name` : deux rognages
    # du même clip peuvent tourner en même temps (double-clic sur « Rogner »,
    # ou relance d'un job qui semble figé alors qu'il encode toujours). Avec
    # un nom partagé, le `unlink` préventif de l'un supprimait le fichier en
    # cours d'écriture de l'autre — sous POSIX le ffmpeg visé continuait
    # d'écrire sur l'inode détaché pendant qu'un second fichier était recréé
    # au même chemin, et le premier processus à finir remplaçait le clip
    # d'origine par le mp4 PARTIEL de l'autre. Un nom unique élimine le
    # besoin même de cet unlink préventif, qui était le mécanisme dangereux.
    temp = temp_dir / f"{path.stem}.{os.getpid()}{path.suffix}"

    args = ffmpeg_trim_args(path, temp, start, end)
    log(f"Rognage de {path.name} : {start:.2f} s → {end:.2f} s "
        f"({end - start:.2f} s)…")
    # Le temporaire n'est gardé que dans UN cas : ffmpeg a produit un fichier
    # bon et c'est le `replace` qui a été refusé. C'est le mode d'échec attendu
    # sous Windows (antivirus, indexeur, lecteur d'aperçu, génération en cours
    # qui tient un handle) et le seul où jeter le temporaire jetterait deux
    # minutes d'encodage réussi.
    garder_temporaire = False
    try:
        # Pas de check=True : un CalledProcessError n'expose que le code retour,
        # jamais le stderr de ffmpeg — or c'est ce message que l'utilisateur
        # doit voir dans le journal. Même motif que beatsync._run_ffmpeg.
        result = subprocess.run(["ffmpeg", *args], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg a échoué :\n  ffmpeg {' '.join(args)}\n"
                               f"{result.stderr}")
        # Un code retour nul ne dit PAS que le fichier produit est complet.
        # ffmpeg traite une erreur de démuxage rencontrée en cours de flux
        # comme une fin de fichier : il journalise (« partial file »,
        # « Invalid NAL unit size »), s'arrête là où il en est, et sort en 0.
        # Sur un clip dont une zone est illisible (téléchargement interrompu
        # puis repris, flux AV1 abîmé), un rognage [5, 180] rendait ainsi 27 s
        # de vidéo, promues en silence par-dessus l'original — 140 s de rushes
        # détruites, avec un « OK » dans le journal et un toast de succès.
        # `-xerror` ne suffirait pas : il ne couvre pas le `partial file` du
        # démuxeur. Le contrôle de durée, si. Marge END_TOLERANCE : la
        # dernière frame ne tombe jamais pile sur la borne demandée.
        produite = probe_duration(temp)
        attendue = end - start
        if produite < attendue - END_TOLERANCE:
            raise RuntimeError(
                f"source illisible : ffmpeg n'a produit que {produite:.2f} s "
                f"au lieu de {attendue:.2f} s (le fichier s'interrompt en cours "
                f"de lecture). {path.name} est INCHANGÉ. Réimporte le clip "
                f"avant de le rogner.")
        # Un code retour nul ne garantit que la fin du process ffmpeg, pas que
        # ses données ont quitté le cache du système de fichiers. Sur une
        # coupure de courant juste après le `replace`, le renommage peut être
        # journalisé sans que le contenu du nouveau fichier le soit : au
        # redémarrage, un clip vide ou tronqué remplacerait un original déjà
        # perdu. ext4 masque en partie ce risque par heuristique ; APFS et
        # NTFS (la prod tourne sous Windows) ne donnent pas cette garantie.
        # "rb+" et non "rb" : sous Windows, os.fsync appelle FlushFileBuffers,
        # qui exige un handle ouvert en écriture et échoue sinon avec
        # ERROR_ACCESS_DENIED — silencieux sur macOS/Linux (fsync y réussit
        # même en lecture seule), donc invisible en local et fatal en prod
        # (la tour tourne sous Windows). Ne pas remettre "rb" : rien n'est
        # écrit ici, mais le mode d'ouverture doit rester "rb+".
        with open(temp, "rb+") as f:
            os.fsync(f.fileno())
        # `replace` est atomique sur le même volume : à aucun instant le clip
        # n'est ni l'ancien ni le nouveau. Cette atomicité protège des autres
        # PROCESSUS, pas du cache disque — d'où le fsync juste au-dessus.
        try:
            temp.replace(path)
        except OSError as exc:
            # Le fichier produit est bon : le nommer pour que l'utilisateur
            # puisse le récupérer à la main (ou relancer une fois le verrou
            # levé) plutôt que de recommencer deux minutes d'encodage avec la
            # même probabilité d'échec.
            garder_temporaire = True
            raise RuntimeError(
                f"remplacement de {path.name} refusé ({exc}) — un autre "
                f"programme tient le fichier (lecteur, antivirus, indexeur, "
                f"ou une génération en cours). Le clip d'origine est intact "
                f"et le fichier rogné est conservé ici : {temp}") from exc
        log(f"OK — {path.name} rogné")
    finally:
        if not garder_temporaire:
            try:
                temp.unlink(missing_ok=True)
            except OSError as exc:
                # Un `unlink` refusé APRÈS un `replace` réussi ne dit rien du
                # rognage, qui a abouti : le laisser remonter transformerait
                # un succès en échec sous les yeux de l'utilisateur. On le
                # signale dans le journal et rien de plus.
                log(f"(temporaire {temp.name} non supprimé : {exc})")
        # Pas de rmdir sur temp_dir : clips/.trim/ est partagé par tous les
        # rognages en cours, y compris ceux de clips DIFFÉRENTS. Si celui-ci
        # est le dernier à finir mais qu'un autre rognage vient tout juste de
        # créer le dossier et de lancer son ffmpeg (spawn + ouverture d'un
        # AV1 de plusieurs minutes, pas instantané), un rmdir ici supprimerait
        # sous ses pieds le dossier où il s'apprête à écrire — échec non
        # reproductible, sans rapport apparent avec ce rognage-ci. Un dossier
        # `.trim` vide est de toute façon invisible de `load_clips` et
        # `/api/state` : c'est exactement la propriété recherchée pour le
        # DOSSIER, donc le laisser traîner ne coûte rien. Son CONTENU, lui,
        # coûte des centaines de mégaoctets par temporaire mort et hérite de
        # la même invisibilité — d'où le `purge_temporaires` en tête de
        # rognage, qui nettoie ce que ce `finally` ne peut pas voir (les
        # temporaires d'exécutions tuées avant d'y arriver).


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit("usage : python trim_clip.py <nom-du-clip> <début> <fin> [<racine>]")
    name = Path(sys.argv[1]).name   # neutralise toute traversée de chemin
    root = Path(sys.argv[4]) if len(sys.argv) > 4 else ROOT
    path = root / "clips" / name
    if not path.is_file():
        sys.exit(f"clip introuvable : {name}")
    try:
        trim_clip(path, sys.argv[2], sys.argv[3],
                  log=lambda m: print(m, flush=True))
    except (ValueError, RuntimeError, OSError) as exc:
        # OSError couvre le `replace` refusé (lecteur, indexeur ou antivirus
        # qui tient un handle sur le clip, fréquent sous Windows) et
        # l'absence de ffmpeg/ffprobe (FileNotFoundError) : un message plutôt
        # qu'un traceback, même si le code retour reste non nul dans les deux cas.
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
