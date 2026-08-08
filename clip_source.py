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
# Combien de candidats on demande au LLM par rapport à ce qu'on garde : le
# classement a besoin de matière à trier, et le recalage en rejette une partie.
# Ce n'est un « +50 % » que sur une source courte (une seule fenêtre de
# transcript) : `propose_moments` impose un plancher de 2 candidats PAR FENÊTRE,
# qui l'emporte dès qu'il y en a plus de 6 — d'où le plafond de notation
# ci-dessous, sans quoi rien ne bornerait le nombre d'appels LLM.
OVERSHOOT = 1.5
# Candidats notés au maximum, par clip demandé. Chaque notation est un appel LLM
# d'une poignée de secondes : sur une source d'une heure à digest_chars=1000,
# ~82 fenêtres proposent ~246 candidats, soit des dizaines de minutes de
# notation pour un lot de 8 clips. Trois fois le lot laisse largement de quoi
# classer.
SCORE_BUDGET = 3


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
    words = None
    if cache.is_file():
        try:
            words = json.loads(cache.read_text(encoding="utf-8"))
            log("Transcript en cache.")
        except (json.JSONDecodeError, OSError):
            # Un processus tué en pleine écriture laisse un JSON tronqué : un
            # cache illisible vaut cache absent, sinon la source est condamnée
            # (cache.is_file() resterait vrai pour toujours et on ne
            # retranscrirait plus jamais).
            log("Transcript en cache corrompu, retranscription.")
    if words is None:
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
    # `log` est transmis aux deux appels LLM : ils dégradent en silence par
    # conception, et sans ça la vraie cause (contexte dépassé, serveur éteint,
    # endpoint erroné) n'apparaissait nulle part.
    digest_chars = int(config.get("digest_chars",
                                  clipper.DEFAULTS["digest_chars"]))
    raw = clipper.propose_moments(words, int(count * OVERSHOOT), SEED,
                                  digest_chars=digest_chars,
                                  min_dur=float(config["min_dur"]),
                                  max_dur=float(config["max_dur"]), log=log)
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

    # 4. Notation, sur le texte recalé et non sur le brut. Un appel LLM par
    # candidat, donc borné : au-delà du budget, on garde les premiers dans
    # l'ordre des fenêtres (couverture chronologique de la source) et on DIT
    # combien tombent — le projet interdit les troncatures silencieuses.
    budget = count * SCORE_BUDGET
    if len(candidates) > budget:
        log(f"{len(candidates) - budget} candidat(s) écarté(s) faute de budget "
            f"de notation (plafond {budget} = {count} clips × {SCORE_BUDGET}).")
        candidates = candidates[:budget]
    # Progression journalisée comme la boucle de rendu : sans ça, le journal
    # restait muet pendant des dizaines de minutes en statut `analyzing`, et
    # l'utilisateur ne voyait que « rien ne se passe ».
    for i, moment in enumerate(candidates, 1):
        log(f"[{i}/{len(candidates)}] notation…")
        text = clipper.moment_text(words, moment["start"], moment["end"])
        moment.update(clipper.score_moment(text, moment["title"], SEED, log=log))

    # 5. Classement.
    best = clipper.rank_moments(candidates, count)
    log(f"{len(best)} clip(s) retenu(s).")

    # Sortie anticipée AVANT toute suppression : si rien n'est retenu, les
    # clips d'un run précédent doivent rester intacts. Sans ça, relancer une
    # analyse avec le LLM éteint effaçait un bon lot et affichait « done ».
    if not best:
        # Le message part dans la colonne `error`, affichée en rouge par l'UI :
        # il renvoie au journal, seul endroit où l'erreur exacte du LLM est
        # écrite. On reste factuel, la cause n'est pas devinable ici.
        reason = ("aucun candidat proposé par le LLM : voir le journal du job "
                  "pour l'erreur exacte" if not raw else
                  "aucun candidat n'a survécu au recalage")
        dbmod.set_clipper_source_status(conn, source_id, "failed", reason)
        log(f"Échec : {reason}.")
        return 0

    # 6. Rendu. Les clips d'une analyse précédente sont remplacés : relancer une
    # source doit donner son résultat courant, pas s'y accumuler.
    # Cas accepté : si TOUS les rendus échouent plus bas, ces clips précédents
    # sont perdus quand même. On ne peut pas s'en protéger proprement sans
    # complexifier (il faudrait rendre dans un dossier temporaire puis
    # basculer) — et une source dont tous les rendus échouent est de toute
    # façon dans un état cassé ; le statut `failed` posé juste après le
    # dit clairement, au lieu de laisser croire à un succès silencieux.
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

    if produced > 0:
        dbmod.set_clipper_source_status(conn, source_id, "done")
        log(f"OK — {produced}/{len(best)} clip(s) produit(s)")
    else:
        dbmod.set_clipper_source_status(
            conn, source_id, "failed",
            "tous les rendus ont échoué : voir le journal")
        log("Échec : tous les rendus ont échoué.")
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
