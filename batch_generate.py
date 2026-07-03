"""batch_generate — décline un plan de publication en vidéos + sidecars JSON.

Plan TOML (posts × comptes) → une vidéo PAR COMPTE (seed dérivée, donc
variante différente du même morceau) déposée dans queue/pending/ avec ses
métadonnées de publication. Le worker de l'étape B/C consommera cette file.
"""

import argparse
import json
import random
import re
import sys
import zlib
from datetime import datetime, timedelta
from pathlib import Path


def build_jobs(plan: dict) -> list[dict]:
    """Expanse posts × comptes en jobs, défauts fusionnés. Logique pure."""
    defaults = plan.get("defaults", {})
    jobs: list[dict] = []
    for post in plan.get("posts", []):
        missing = [k for k in ("track", "accounts", "date", "time") if k not in post]
        if missing:
            raise ValueError(f"post incomplet ({post.get('track', '?')}) : champs manquants {missing}")
        for account in post["accounts"]:
            jobs.append(
                {
                    "track": post["track"],
                    "title": post.get("title", Path(post["track"]).stem),
                    "account": account,
                    "date": post["date"],
                    "time": post["time"],
                    "duration": post.get("duration", defaults.get("duration", 30)),
                    "caption_template": post.get("caption", defaults.get("caption", "{title}")),
                    "hashtags": post.get("hashtags", defaults.get("hashtags", [])),
                }
            )
    return jobs


def derive_seed(track: str, account: str, date: str) -> int:
    """Seed stable par (morceau, compte, date) : chaque compte reçoit sa
    propre variante, reproductible d'une exécution à l'autre."""
    return zlib.crc32(f"{track}|{account}|{date}".encode()) & 0xFFFFFF


def make_caption(template: str, title: str, hashtags: list[str]) -> str:
    caption = template.format(title=title)
    if hashtags:
        caption += " " + " ".join(f"#{tag}" for tag in hashtags)
    return caption


def schedule_time(date: str, time: str, seed: int) -> str:
    """Heure de publication avec jitter déterministe (0-14 min, 0-59 s) :
    des posts pile à heure fixe tous les jours, ça ressemble à un robot."""
    rng = random.Random(seed)
    base = datetime.fromisoformat(f"{date}T{time}")
    return (base + timedelta(minutes=rng.randint(0, 14), seconds=rng.randint(0, 59))).isoformat()


def output_stem(job: dict, seed: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", job["title"].lower()).strip("-")
    time_part = job["time"].replace(":", "-")
    return f"{job['date']}_{time_part}_{job['account']}_{slug}_s{seed}"


def load_plan(path: Path) -> dict:
    import tomllib

    with open(path, "rb") as f:
        return tomllib.load(f)


def generate(jobs: list[dict], clips_dir: Path, queue_dir: Path) -> list[Path]:
    """Génère les vidéos manquantes de la file (relance idempotente : ce qui
    existe déjà n'est pas re-rendu). Analyse audio et scan des clips partagés."""
    from beatsync import (DEFAULT_CONFIG, analyze_audio, build_edl, load_clips,
                          render, resolve_window, scan_clips)

    pending = queue_dir / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    print(f"Scan des clips de {clips_dir}…")
    clips = scan_clips(load_clips(clips_dir))
    analyses: dict[str, dict] = {}
    created: list[Path] = []
    for job in jobs:
        seed = derive_seed(job["track"], job["account"], job["date"])
        stem = output_stem(job, seed)
        video = pending / f"{stem}.mp4"
        sidecar = pending / f"{stem}.json"
        if video.exists() and sidecar.exists():
            print(f"  déjà en file : {stem}")
            continue
        if job["track"] not in analyses:
            print(f"Analyse de {job['track']}…")
            analyses[job["track"]] = analyze_audio(Path(job["track"]))
        analysis = analyses[job["track"]]
        config = resolve_window(analysis, dict(DEFAULT_CONFIG), duration=job["duration"])
        edl = build_edl(analysis, clips, config, seed=seed)
        print(f"  rendu {stem} ({len(edl)} segments, "
              f"{config['end'] - config['start']:.1f} s)…")
        render(edl, Path(job["track"]), video, config)
        sidecar.write_text(
            json.dumps(
                {
                    "video": video.name,
                    "account": job["account"],
                    "scheduled_at": schedule_time(job["date"], job["time"], seed),
                    "caption": make_caption(job["caption_template"], job["title"], job["hashtags"]),
                    "track": job["track"],
                    "title": job["title"],
                    "seed": seed,
                    "status": "pending",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        created.append(video)
    return created


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Décline un plan de publication TOML en vidéos + métadonnées dans queue/pending/."
    )
    parser.add_argument("plan", nargs="?", default="plan.toml",
                        help="plan de publication (défaut : plan.toml, voir plan.example.toml)")
    parser.add_argument("--clips", default="clips", help="dossier des clips (défaut : clips/)")
    parser.add_argument("--queue", default="queue", help="racine de la file (défaut : queue/)")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    if not plan_path.is_file():
        sys.exit(f"plan introuvable : {plan_path} (copie plan.example.toml vers plan.toml)")
    jobs = build_jobs(load_plan(plan_path))
    missing = sorted({j["track"] for j in jobs if not Path(j["track"]).is_file()})
    if missing:
        sys.exit("morceaux introuvables :\n  " + "\n  ".join(missing))

    print(f"{len(jobs)} vidéo(s) au plan")
    created = generate(jobs, Path(args.clips), Path(args.queue))
    print(f"OK : {len(created)} nouvelle(s) vidéo(s) dans {args.queue}/pending/")


if __name__ == "__main__":
    main()
