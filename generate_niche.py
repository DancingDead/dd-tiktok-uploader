"""generate_niche — génère un lot de N variantes pour une niche.

Chaque variante = un morceau (parmi ceux sélectionnés dans la niche) + une seed
distincte → montage ET punchlines différents. Les presets liés sont alternés.
Lancé par l'interface en tâche de fond ; les vidéos produites sont enregistrées
en base (status « proposed ») dans la bibliothèque de la niche.

    uv run python generate_niche.py <niche_id> <count> [<root>]
"""

import random
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent


def plan_variants(tracks: list[str], count: int, base_seed: int) -> list[tuple[str, int]]:
    """Pour chaque variante : (morceau, seed) — déterministe à base_seed égal,
    seeds distinctes, morceaux tirés parmi ceux de la niche."""
    if not tracks or count <= 0:
        return []
    rng = random.Random(base_seed)
    seeds, used = [], set()
    while len(seeds) < count:
        s = rng.randrange(1, 1_000_000)
        if s not in used:
            used.add(s)
            seeds.append(s)
    return [(rng.choice(tracks), s) for s in seeds]


def video_stem(slug: str, track: str, seed: int, created_at: str) -> str:
    tname = re.sub(r"[^a-z0-9]+", "-", Path(track).stem.lower()).strip("-")[:40]
    return f"{created_at.replace(':', '-')}_{slug}_{tname}_s{seed}"


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage : python generate_niche.py <niche_id> <count> [<root>]")
    niche_id, count = int(sys.argv[1]), int(sys.argv[2])
    root = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT

    import db as dbmod
    from beatsync import generate_video, load_clips, load_settings, merge_settings, scan_clips

    conn = dbmod.connect(root / "platform.db")
    niche = dbmod.get_niche(conn, niche_id)
    if niche is None:
        sys.exit(f"niche #{niche_id} introuvable")
    if not niche["tracks"]:
        sys.exit("cette niche n'a aucun morceau sélectionné")
    if not niche["clips"]:
        sys.exit("cette niche n'a aucun clip sélectionné")

    # Clips sélectionnés dans le catalogue partagé (root/clips), scannés une fois.
    selected = {Path(c).name for c in niche["clips"]}
    clips = [c for c in load_clips(root / "clips") if c["path"].name in selected]
    if not clips:
        sys.exit("aucun clip sélectionné n'est présent dans le catalogue partagé")
    print(f"Scan de {len(clips)} clip(s) sélectionné(s)…", flush=True)
    scan_clips(clips, cache_dir=root / "data" / "cache" / "scan")

    preset_by_id = {p["id"]: p for p in dbmod.list_presets(conn)}
    niche_presets = [preset_by_id[pid] for pid in niche["preset_ids"] if pid in preset_by_id]

    variants = plan_variants(niche["tracks"], count, random.randrange(1, 1_000_000))
    videos_dir = dbmod.niche_videos_dir(root / "data", niche["slug"])
    videos_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now().isoformat(timespec="seconds")

    produced = 0
    for i, (track, seed) in enumerate(variants, 1):
        preset = niche_presets[(i - 1) % len(niche_presets)] if niche_presets else None
        config = merge_settings(load_settings(root / "settings.json"),
                                preset["overrides"] if preset else {})
        config["subtitles"] = {**config["subtitles"], **niche["subtitles"]}
        stem = video_stem(niche["slug"], track, seed, created_at)
        out = videos_dir / f"{stem}.mp4"
        label = f"[{i}/{count}] {Path(track).name} · seed {seed}"
        print(label + (f" · preset « {preset['name']} »" if preset else ""), flush=True)
        try:
            info = generate_video(track, clips, config, seed=seed, output_path=out,
                                  subtitles_cache_dir=root / "data" / "cache" / "subtitles",
                                  log=lambda m: print(m, flush=True))
        except Exception as exc:
            print(f"  échec : {exc}", flush=True)
            continue
        dbmod.create_video(
            conn, niche_id=niche_id, track=track, seed=seed,
            file=str(out.relative_to(root)),
            preset_id=(preset["id"] if preset else None),
            caption=niche["caption_template"],
            subtitles={"lines": info["captions"]},
            created_at=datetime.now().isoformat(timespec="seconds"))
        produced += 1
    # Compte réel (≠ tentées) : un échec par variante était silencieux, la sortie
    # restait « OK » en code 0 → l'UI annonçait un succès pour 0 vidéo produite.
    print(f"OK — {produced}/{len(variants)} variante(s) produite(s)", flush=True)
    if variants and produced == 0:
        # Échec total → code retour non nul → le job passe « failed » côté UI.
        sys.exit("échec : aucune variante n'a pu être produite (voir le journal)")


if __name__ == "__main__":
    main()
