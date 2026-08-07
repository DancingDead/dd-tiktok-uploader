"""clip_source — extrait les meilleurs shorts d'une source longue.

Orchestrateur du clipper : transcription (mise en cache) → proposition LLM →
recalage → notation → classement → rendu. Lancé en tâche de fond par l'UI, comme
generate_niche.py. Écrit le statut en base à chaque étape pour que l'interface
suive l'avancement.

    uv run python clip_source.py <source_id> [<root>]
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
# Seed fixe : la reproductibilité vaut au niveau de la source. Relancer une
# analyse doit rendre les mêmes clips, sans quoi on ne peut pas comparer un
# réglage à un autre.
SEED = 1337
# On demande 50 % de candidats en plus que ce qu'on garde : le classement a
# besoin de matière à trier, et le recalage en rejette une partie.
OVERSHOOT = 1.5


def slug_for(title: str, existing: set[str]) -> str:
    """Slug de dossier unique. Pure."""
    base = re.sub(r"[^a-z0-9]+", "-",
                  _ascii(title).lower()).strip("-")[:60] or "source"
    slug, n = base, 1
    while slug in existing:
        n += 1
        slug = f"{base}-{n}"
    return slug


def _ascii(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def process(conn, root: Path, source_id: int, config: dict, log=print) -> int:
    """Déroule le pipeline pour une source. Retourne le nombre de clips produits."""
    import clipper
    import db as dbmod

    source = dbmod.get_clipper_source(conn, source_id)
    if source is None:
        raise KeyError(f"source #{source_id} introuvable")
    video = root / source["path"]
    folder = dbmod.clipper_source_dir(root / "data", source["slug"])
    folder.mkdir(parents=True, exist_ok=True)

    # 1. Transcription — l'étape la plus lente de loin, donc mise en cache sur
    # disque : relancer une analyse après un changement de réglage ne doit pas
    # repayer une heure de Whisper.
    cache = folder / "transcript.json"
    if cache.is_file():
        log("Transcript en cache.")
        words = json.loads(cache.read_text(encoding="utf-8"))
    else:
        dbmod.set_clipper_source_status(conn, source_id, "transcribing")
        log("Transcription en cours (c'est long, ~1x la durée de la vidéo)…")
        words = clipper.transcribe(video, config["whisper_model"])
        cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    if not words:
        dbmod.set_clipper_source_status(
            conn, source_id, "failed",
            "aucune parole détectée : le lot 1 du clipper ne traite que le "
            "contenu parlé")
        log("Échec : aucune parole détectée.")
        return 0
    log(f"{len(words)} mots transcrits.")

    # 2-3. Proposition puis recalage.
    dbmod.set_clipper_source_status(conn, source_id, "analyzing")
    count = int(config["clip_count"])
    raw = clipper.propose_moments(words, int(count * OVERSHOOT), SEED)
    log(f"{len(raw)} candidat(s) proposé(s) par le LLM.")

    candidates = []
    for moment in raw:
        snapped = clipper.snap_to_speech(
            moment["start"], moment["end"], words,
            float(config["min_dur"]), float(config["max_dur"]))
        if snapped is None:
            continue   # ne rentre pas dans les bornes : un clip de moins, pas un échec
        candidates.append({**moment, "start": snapped[0], "end": snapped[1]})
    log(f"{len(candidates)} candidat(s) après recalage.")

    # 4. Notation, sur le texte recalé et non sur le brut.
    for moment in candidates:
        text = clipper.moment_text(words, moment["start"], moment["end"])
        moment.update(clipper.score_moment(text, moment["title"], SEED))

    # 5. Classement.
    best = clipper.rank_moments(candidates, count)
    log(f"{len(best)} clip(s) retenu(s).")

    # 6. Rendu. Les clips d'une analyse précédente sont remplacés : relancer une
    # source doit donner son résultat courant, pas s'y accumuler.
    for old in dbmod.list_clipper_clips(conn, source_id):
        dbmod.delete_clipper_clip(conn, old["id"])
        old_file = root / old["file"]
        if old_file.is_file():
            old_file.unlink()

    dbmod.set_clipper_source_status(conn, source_id, "rendering")
    produced = 0
    for i, moment in enumerate(best, 1):
        stem = f"{i:02d}-" + (re.sub(r"[^a-z0-9]+", "-",
                                     _ascii(moment["title"]).lower()).strip("-")[:40]
                              or "clip")
        out = folder / "clips" / f"{stem}.mp4"
        log(f"[{i}/{len(best)}] {moment['title']} · score {moment['score']:.0f}")
        try:
            clipper.render_clip(video, moment["start"], moment["end"], out,
                                words=words, config=config)
        except Exception as exc:
            log(f"  échec du rendu : {exc}")
            continue   # un clip raté ne fait pas perdre les autres
        dbmod.create_clipper_clip(
            conn, source_id=source_id, start=moment["start"], end=moment["end"],
            title=moment["title"], hook=moment["hook"], flow=moment["flow"],
            value=moment["value"], score=moment["score"], why=moment["why"],
            file=str(out.relative_to(root)))
        produced += 1

    dbmod.set_clipper_source_status(conn, source_id, "done")
    log(f"OK — {produced}/{len(best)} clip(s) produit(s)")
    return produced


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage : python clip_source.py <source_id> [<root>]")
    source_id = int(sys.argv[1])
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT

    import clipper
    import db as dbmod
    from beatsync import load_settings

    conn = dbmod.connect(root / "platform.db")
    config = {**clipper.DEFAULTS,
              **(load_settings(root / "settings.json").get("clipper") or {})}
    try:
        produced = process(conn, root, source_id, config,
                           log=lambda m: print(m, flush=True))
    except Exception as exc:
        dbmod.set_clipper_source_status(conn, source_id, "failed", str(exc))
        raise
    # Code retour non nul → le job passe « failed » côté UI, comme generate_niche.
    if produced == 0:
        sys.exit("échec : aucun clip produit (voir le journal)")


if __name__ == "__main__":
    main()
