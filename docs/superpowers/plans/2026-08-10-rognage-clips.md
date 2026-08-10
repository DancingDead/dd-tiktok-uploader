# Rognage des clips du catalogue — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Depuis l'onglet Catalogue, couper le début et la fin d'un clip du catalogue partagé pour en retirer l'intro et l'outro, en voyant ce qu'on coupe.

**Architecture :** Un script indépendant `trim_clip.py`, sur le moule de `fetch_tracks.py` et `generate_niche.py` : deux fonctions pures (validation des bornes, arguments FFmpeg) et une fonction d'I/O qui réencode vers un fichier temporaire puis remplace l'original. Lancé en tâche de fond par l'interface, parce qu'un réencodage AV1 dure plusieurs minutes. Une modale React avec le lecteur d'aperçu déjà en place permet de poser les bornes.

**Tech Stack :** Python 3.11+, uv, FFmpeg via `subprocess`, Flask, React + shadcn/ui.

**Spec :** `docs/superpowers/specs/2026-08-10-rognage-clips-design.md`

## Global Constraints

- **uv, jamais pip.** `uv run pytest` pour tester, `uv run python` pour lancer. Le venv n'a pas de module pip.
- **Tout en français** : code, commentaires, interface, messages de commit. Noms de fonctions et de variables en anglais.
- **Les commentaires expliquent le POURQUOI.** Chaque valeur non évidente porte la raison de son choix — c'est le standard de `beatsync.py` et `clipper.py`, reproduis-le.
- **FFmpeg par `subprocess`**, jamais `ffmpeg-python` (non maintenu).
- **Pureté** : `coerce_bounds` et `ffmpeg_trim_args` ne font aucune I/O, n'utilisent ni horloge ni RNG, et ne mutent pas leurs arguments.
- **Défense en profondeur côté serveur** : coercion de toute valeur reçue → **400, jamais 500**. Garde anti-traversal sur tout chemin de fichier.
- **Valeurs d'encodage imposées par la spec** : `libx264`, `crf` = **18** (un cran au-dessus du CRF 20 du rendu final : ce clip est un intermédiaire qui sera réencodé une seconde fois), `preset` = `medium`, `-c:a copy`, `-pix_fmt yuv420p`, flags `bitexact`, `-ss`/`-to` **avant** `-i`.
- **`MIN_TRIMMED_DUR` = 1,0 s** : en dessous, le clip n'a plus de matière et `usable_intervals` le rejetterait de toute façon.
- **L'opération est destructive et irréversible.** Le remplacement du fichier n'a lieu qu'après un code retour FFmpeg nul.
- **Commits fréquents**, un par tâche minimum, format `feat(trim): …` / `test(trim): …` / `docs(trim): …`.

---

## Structure des fichiers

**Créés :**

| Fichier | Responsabilité |
|---|---|
| `trim_clip.py` | Validation des bornes, arguments FFmpeg, réencodage atomique, CLI |
| `tests/test_trim_clip.py` | Les deux fonctions pures |
| `frontend/src/features/catalogue/TrimDialog.tsx` | La modale : lecteur, bornes, confirmation |

**Modifiés :**

| Fichier | Changement |
|---|---|
| `webui.py` | l'endpoint `POST /api/clips/<name>/trim` |
| `tests/test_webui_platform.py` | les tests de l'endpoint |
| `frontend/src/lib/api.ts` | la méthode `trimClip` |
| `frontend/src/features/catalogue/AssetSection.tsx` | l'action « Rogner », **seulement** pour la section Clips |
| `frontend/src/features/catalogue/Catalogue.tsx` | branchement de la modale |
| `CLAUDE.md` | l'endpoint et le script dans les paragraphes concernés |

---

### Task 1 : Les deux fonctions pures de `trim_clip.py`

**Files:**
- Create: `trim_clip.py`
- Test: `tests/test_trim_clip.py`

**Interfaces:**
- Consumes: rien
- Produces:
  - `trim_clip.MIN_TRIMMED_DUR = 1.0`, `trim_clip.TRIM_CRF = 18`, `trim_clip.TRIM_PRESET = "medium"`
  - `trim_clip.coerce_bounds(start, end, duration) -> tuple[float, float]` — lève `ValueError` si invalide
  - `trim_clip.ffmpeg_trim_args(source: Path, target: Path, start: float, end: float) -> list[str]` — les arguments **après** le binaire `ffmpeg`

- [ ] **Step 1 : Écrire les tests qui échouent**

Crée `tests/test_trim_clip.py` :

```python
from pathlib import Path

import pytest

from trim_clip import TRIM_CRF, TRIM_PRESET, coerce_bounds, ffmpeg_trim_args


def test_bornes_nominales():
    assert coerce_bounds(3.0, 20.0, 30.0) == (3.0, 20.0)


def test_les_bornes_acceptent_des_chaines():
    """L'interface envoie du JSON, les nombres peuvent arriver en texte."""
    assert coerce_bounds("3", "20.5", "30") == (3.0, 20.5)


def test_une_fin_qui_depasse_a_peine_est_ramenee_a_la_duree():
    """La derniere frame d'une video tombe rarement pile sur la duree annoncee :
    poser la borne de fin sur la fin du lecteur ne doit pas etre un echec."""
    assert coerce_bounds(0.0, 30.04, 30.0) == (0.0, 30.0)


def test_un_debut_legerement_negatif_est_ramene_a_zero():
    assert coerce_bounds(-0.01, 10.0, 30.0) == (0.0, 10.0)


def test_bornes_inversees():
    with pytest.raises(ValueError):
        coerce_bounds(20.0, 3.0, 30.0)


def test_bornes_egales():
    with pytest.raises(ValueError):
        coerce_bounds(10.0, 10.0, 30.0)


def test_duree_resultante_trop_courte():
    """Sous MIN_TRIMMED_DUR le clip n'a plus de matiere exploitable."""
    with pytest.raises(ValueError):
        coerce_bounds(10.0, 10.5, 30.0)


def test_un_debut_franchement_negatif_est_refuse():
    """Ramener -5 a 0 masquerait une erreur de saisie au lieu de la signaler."""
    with pytest.raises(ValueError):
        coerce_bounds(-5.0, 10.0, 30.0)


def test_une_fin_franchement_au_dela_de_la_duree_est_refusee():
    with pytest.raises(ValueError):
        coerce_bounds(0.0, 45.0, 30.0)


def test_valeurs_non_numeriques():
    with pytest.raises(ValueError):
        coerce_bounds("<script>", 10.0, 30.0)
    with pytest.raises(ValueError):
        coerce_bounds(0.0, None, 30.0)


def test_les_booleens_sont_refuses():
    """float(True) vaut 1.0 : accepter un booleen ferait passer une faute de
    frappe pour une borne valide."""
    with pytest.raises(ValueError):
        coerce_bounds(True, 10.0, 30.0)


def test_valeurs_non_finies():
    with pytest.raises(ValueError):
        coerce_bounds(float("nan"), 10.0, 30.0)
    with pytest.raises(ValueError):
        coerce_bounds(0.0, float("inf"), 30.0)


def test_le_seek_est_avant_l_entree():
    """-ss apres -i ferait decoder toute la video avant la coupe."""
    args = ffmpeg_trim_args(Path("clips/a.mp4"), Path("clips/.trim/a.mp4"), 3.0, 20.0)
    assert args.index("-ss") < args.index("-i")
    assert args.index("-to") < args.index("-i")


def test_les_valeurs_d_encodage_sont_celles_de_la_spec():
    args = ffmpeg_trim_args(Path("a.mp4"), Path("b.mp4"), 0.0, 10.0)
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-crf") + 1] == str(TRIM_CRF)
    assert args[args.index("-preset") + 1] == TRIM_PRESET
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-c:a") + 1] == "copy"


def test_les_flags_bitexact_sont_presents():
    """Invariant du projet : sans eux, l'encodeur date le fichier et deux
    encodages identiques different octet a octet."""
    args = ffmpeg_trim_args(Path("a.mp4"), Path("b.mp4"), 0.0, 10.0)
    assert "+bitexact" in args


def test_la_source_et_la_cible_sont_aux_bons_endroits():
    args = ffmpeg_trim_args(Path("clips/a.mp4"), Path("clips/.trim/a.mp4"), 1.0, 5.0)
    assert args[args.index("-i") + 1] == "clips/a.mp4"
    assert args[-1] == "clips/.trim/a.mp4"
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

```bash
uv run pytest tests/test_trim_clip.py -v
```

Attendu : `ModuleNotFoundError: No module named 'trim_clip'`.

- [ ] **Step 3 : Écrire les deux fonctions pures**

Crée `trim_clip.py` :

```python
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
import subprocess
import sys
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
        # Mêmes flags que le reste du projet : sans eux, l'encodeur date le
        # fichier et deux encodages identiques diffèrent octet à octet.
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
        str(target),
    ]
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

```bash
uv run pytest tests/test_trim_clip.py -v && uv run pytest -q
```

Attendu : 16 nouveaux tests PASS, suite complète verte.

- [ ] **Step 5 : Commit**

```bash
git add trim_clip.py tests/test_trim_clip.py
git commit -m "feat(trim): validation des bornes et arguments ffmpeg du rognage"
```

---

### Task 2 : Le réencodage atomique et la CLI

Le remplacement du fichier est la partie dangereuse : une interruption au mauvais moment détruirait le clip au lieu de le rogner.

**Files:**
- Modify: `trim_clip.py`
- Test: vérification manuelle (voir Step 4)

**Interfaces:**
- Consumes: `coerce_bounds`, `ffmpeg_trim_args` de la Task 1
- Produces:
  - `trim_clip.TRIM_DIR_NAME = ".trim"`
  - `trim_clip.probe_duration(path: Path) -> float`
  - `trim_clip.trim_clip(path: Path, start, end, log=print) -> None`
  - CLI : `python trim_clip.py <nom-du-clip> <début> <fin> [<racine>]`

- [ ] **Step 1 : Écrire l'I/O et la CLI**

Ajoute à `trim_clip.py` :

```python
# Le temporaire vit dans un SOUS-DOSSIER de clips/ : même volume, donc le
# remplacement final est un simple rename atomique. Un sous-dossier plutôt
# qu'un fichier voisin parce que `load_clips` et `/api/state` parcourent
# `clips/` et prendraient un `xxx.trim.mp4` oublié pour un vrai clip.
TRIM_DIR_NAME = ".trim"


def probe_duration(path: Path) -> float:
    """Durée du clip en secondes. I/O."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def trim_clip(path: Path, start, end, log=print) -> None:
    """Rogne le clip en place. Le fichier d'origine n'est remplacé qu'après un
    code retour FFmpeg nul : une interruption laisse le clip intact. I/O."""
    start, end = coerce_bounds(start, end, probe_duration(path))
    temp_dir = path.parent / TRIM_DIR_NAME
    temp_dir.mkdir(exist_ok=True)
    temp = temp_dir / path.name
    temp.unlink(missing_ok=True)   # reste d'un rognage interrompu

    args = ffmpeg_trim_args(path, temp, start, end)
    log(f"Rognage de {path.name} : {start:.2f} s → {end:.2f} s "
        f"({end - start:.2f} s)…")
    try:
        # Pas de check=True : un CalledProcessError n'expose que le code retour,
        # jamais le stderr de ffmpeg — or c'est ce message que l'utilisateur
        # doit voir dans le journal. Même motif que beatsync._run_ffmpeg.
        result = subprocess.run(["ffmpeg", *args], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg a échoué :\n  ffmpeg {' '.join(args)}\n"
                               f"{result.stderr}")
        # `replace` est atomique sur le même volume : à aucun instant le clip
        # n'est ni l'ancien ni le nouveau.
        temp.replace(path)
        log(f"OK — {path.name} rogné")
    finally:
        temp.unlink(missing_ok=True)
        # Le dossier temporaire ne sert qu'à ce rognage ; le laisser traînerait
        # un dossier vide dans le catalogue.
        try:
            temp_dir.rmdir()
        except OSError:
            pass   # un autre rognage tourne en parallèle : on lui laisse


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
    except (ValueError, RuntimeError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2 : Vérifier que la suite ne casse pas**

```bash
uv run pytest -q
```

Attendu : suite complète verte.

- [ ] **Step 3 : Vérifier que le fichier est bien réécrit, sur une copie**

**Travaille sur une copie, jamais sur le catalogue de l'utilisateur.**

```bash
mkdir -p /tmp/essai-trim/clips
cp "clips/Demon_Slayer_-_Sad_Scenes_Free_Clips_HD.mp4" /tmp/essai-trim/clips/
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name:format=duration -of csv=p=0 /tmp/essai-trim/clips/*.mp4
uv run python trim_clip.py "Demon_Slayer_-_Sad_Scenes_Free_Clips_HD.mp4" 5 20 /tmp/essai-trim
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name:format=duration -of csv=p=0 /tmp/essai-trim/clips/*.mp4
ls -a /tmp/essai-trim/clips/
```

Attendu : durée passée d'environ 29,9 s à **15 s**, codec `h264`, et **aucun dossier `.trim` résiduel**.

- [ ] **Step 4 : Vérifier sur un clip AV1, celui qui compte**

8 clips sur 11 sont en AV1 ; c'est le cas réel et le plus lent.

```bash
cp "clips/Akaza_vs_Rengoku_4K60FPS_Smoothes.mp4" /tmp/essai-trim/clips/
time uv run python trim_clip.py "Akaza_vs_Rengoku_4K60FPS_Smoothes.mp4" 10 40 /tmp/essai-trim
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height:format=duration -of csv=p=0 /tmp/essai-trim/clips/Akaza*.mp4
```

Attendu : durée **30 s**, codec `h264` (et non plus `av1`), dimensions inchangées. Note le temps écoulé dans ton rapport — c'est ce que l'utilisateur attendra.

- [ ] **Step 5 : Vérifier qu'un échec ne détruit pas le clip**

```bash
cp "clips/The_Beauty_of_Demon_Slayer_4K_EDIT.mp4" /tmp/essai-trim/clips/
ls -l /tmp/essai-trim/clips/The_Beauty*.mp4
uv run python trim_clip.py "The_Beauty_of_Demon_Slayer_4K_EDIT.mp4" 5 5.2 /tmp/essai-trim ; echo "code retour : $?"
ls -l /tmp/essai-trim/clips/The_Beauty*.mp4
```

Attendu : message d'erreur sur la durée minimale, code retour non nul, **fichier inchangé** (même taille).

- [ ] **Step 6 : Commit**

```bash
git add trim_clip.py
git commit -m "feat(trim): reencodage atomique et CLI du rognage"
```

---

### Task 3 : L'endpoint

**Files:**
- Modify: `webui.py`
- Test: `tests/test_webui_platform.py`

**Interfaces:**
- Consumes: `trim_clip.coerce_bounds`, `trim_clip.probe_duration`
- Produces: `POST /api/clips/<name>/trim` avec `{"start": float, "end": float}` → `{"job_id": …}`

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajoute à `tests/test_webui_platform.py` :

```python
def test_rognage_refuse_les_bornes_invalides(client, tmp_path, monkeypatch):
    """400 et pas 500 : le contrat du projet est « coercion serveur »."""
    import webui
    monkeypatch.setattr(webui, "start_job", lambda name, argv: "job1")
    (tmp_path / "clips").mkdir(exist_ok=True)
    (tmp_path / "clips" / "a.mp4").write_bytes(b"faux")
    monkeypatch.setattr("trim_clip.probe_duration", lambda p: 30.0)

    for bornes in ({"start": 20, "end": 3}, {"start": -5, "end": 10},
                   {"start": 0, "end": 45}, {"start": "<script>", "end": 10},
                   {"start": 10, "end": 10.2}):
        r = client.post("/api/clips/a.mp4/trim", json=bornes)
        assert r.status_code == 400, bornes


def test_rognage_lance_un_job_sur_des_bornes_valides(client, tmp_path, monkeypatch):
    import webui
    lances = []
    monkeypatch.setattr(webui, "start_job",
                        lambda name, argv: lances.append((name, argv)) or "job1")
    (tmp_path / "clips").mkdir(exist_ok=True)
    (tmp_path / "clips" / "a.mp4").write_bytes(b"faux")
    monkeypatch.setattr("trim_clip.probe_duration", lambda p: 30.0)

    r = client.post("/api/clips/a.mp4/trim", json={"start": 3, "end": 20})
    assert r.status_code == 200 and r.get_json()["job_id"] == "job1"
    nom, argv = lances[0]
    # Un nom par clip : un nom global interdirait de rogner deux clips
    # differents en parallele.
    assert nom == "trim-a.mp4"
    assert "trim_clip.py" in " ".join(argv)


def test_rognage_d_un_clip_inconnu(client, monkeypatch):
    import webui
    monkeypatch.setattr(webui, "start_job", lambda name, argv: "job1")
    assert client.post("/api/clips/absent.mp4/trim",
                       json={"start": 0, "end": 10}).status_code == 404


def test_rognage_refuse_une_image(client, tmp_path, monkeypatch):
    """Rogner un .jpg n'a pas de sens : une image est montee en flash court,
    sans notion de duree."""
    import webui
    monkeypatch.setattr(webui, "start_job", lambda name, argv: "job1")
    (tmp_path / "clips").mkdir(exist_ok=True)
    (tmp_path / "clips" / "a.jpg").write_bytes(b"faux")
    assert client.post("/api/clips/a.jpg/trim",
                       json={"start": 0, "end": 10}).status_code == 400


def test_rognage_anti_traversal(client, tmp_path, monkeypatch):
    """Un nom trafique ne doit pas atteindre un fichier hors de clips/."""
    import webui
    monkeypatch.setattr(webui, "start_job", lambda name, argv: "job1")
    dehors = tmp_path.parent / "dehors.mp4"
    dehors.write_bytes(b"secret")
    r = client.post("/api/clips/..%2Fdehors.mp4/trim", json={"start": 0, "end": 10})
    assert r.status_code in (400, 404)
    assert dehors.is_file()
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

```bash
uv run pytest tests/test_webui_platform.py -k rognage -v
```

Attendu : 404 sur une route inexistante.

- [ ] **Step 3 : Écrire l'endpoint**

Dans `webui.py`, à l'intérieur de `create_app`, juste après `download_clips` :

```python
    @app.post("/api/clips/<path:name>/trim")
    def trim_clip_ep(name):
        """Rogne le début et la fin d'un clip du catalogue. DESTRUCTIF : le
        fichier est réécrit. Lancé en tâche de fond, un réencodage AV1 durant
        plusieurs minutes."""
        import trim_clip as trimmod

        safe = Path(name).name  # neutralise toute traversée de chemin
        # VIDEO_EXTS et non CLIP_EXTS : ce dernier contient aussi les images,
        # et rogner un .jpg n'a pas de sens (monté en flash court, sans durée).
        if Path(safe).suffix.lower() not in VIDEO_EXTS:
            return jsonify({"error": f"format non rognable : {safe}"}), 400
        base = paths["clips"].resolve()
        target = (base / safe).resolve()
        if target.parent != base:
            return jsonify({"error": "chemin invalide"}), 400
        if not target.is_file():
            return jsonify({"error": "clip introuvable"}), 404

        body = request.json or {}
        try:
            # La durée est lue côté serveur : on ne valide pas des bornes
            # contre une durée fournie par le client.
            start, end = trimmod.coerce_bounds(
                body.get("start"), body.get("end"), trimmod.probe_duration(target))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            return jsonify({"error": "durée du clip illisible"}), 400

        try:
            # Un nom de job par clip : un nom global interdirait de rogner deux
            # clips différents en parallèle. Le 409 protège du double-clic sur
            # le MÊME clip, qui ferait travailler deux ffmpeg sur le même
            # fichier temporaire.
            job_id = start_job(f"trim-{safe}",
                               [sys.executable, "trim_clip.py", safe,
                                f"{start:.3f}", f"{end:.3f}",
                                str(paths["clips"].parent)])
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"job_id": job_id})
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

```bash
uv run pytest tests/test_webui_platform.py -v && uv run pytest -q
```

Attendu : tout vert.

- [ ] **Step 5 : Commit**

```bash
git add webui.py tests/test_webui_platform.py
git commit -m "feat(trim): endpoint de rognage d'un clip"
```

---

### Task 4 : La modale et l'action dans le catalogue

**Files:**
- Create: `frontend/src/features/catalogue/TrimDialog.tsx`
- Modify: `frontend/src/lib/api.ts`, `frontend/src/features/catalogue/AssetSection.tsx`, `frontend/src/features/catalogue/Catalogue.tsx`
- Test: vérification manuelle (le projet n'a pas de tests frontend)

**Interfaces:**
- Consumes: `POST /api/clips/<name>/trim` de la Task 3
- Produces: `api.trimClip(name, start, end)`, le composant `TrimDialog`

- [ ] **Step 1 : Ajouter la méthode au client API**

Dans `frontend/src/lib/api.ts`, dans l'objet `api`, à côté de `deleteClip` :

```ts
  trimClip: (name: string, start: number, end: number) =>
    req<{ job_id: string }>(`/api/clips/${encodeURIComponent(name)}/trim`,
                            json({ start, end })),
```

- [ ] **Step 2 : Écrire `TrimDialog.tsx`**

Modèle à suivre : `frontend/src/components/confirm.tsx` pour la mécanique de modale, et `frontend/src/features/clipper/SourceDetail.tsx` pour un lecteur vidéo dans une carte. Utilise les composants existants de `frontend/src/components/ui/dialog.tsx`, `input.tsx`, `button.tsx`, `label.tsx`.

Le composant reçoit `{ name: string | null; onClose: () => void; onStarted: (jobId: string) => void }` et s'affiche quand `name` n'est pas nul. Il contient :

- un `<video controls src={api.assetUrl("clips/" + name)} />` avec une `ref`, en `max-h-[45vh] w-full` ;
- deux champs numériques **début** et **fin** (en secondes, pas de 0,1), chacun suivi d'un bouton secondaire **« prendre ici »** qui y écrit `videoRef.current.currentTime` arrondi au dixième — c'est ce qui rend l'opération praticable sans noter des timecodes ailleurs ;
- la **durée résultante** affichée sous les champs (`fin − début`, une décimale), en `text-destructive` si elle est inférieure à 1 s ;
- un texte d'avertissement : « Le fichier du catalogue sera réécrit. Cette opération est irréversible ; le clip devra être réimporté depuis son lien YouTube pour revenir en arrière. » ;
- un bouton de confirmation **désactivé** tant que la durée résultante est sous 1 s ou que la fin n'est pas après le début.

À la confirmation, appelle `api.trimClip`, passe le `job_id` à `onStarted`, ferme la modale, et affiche une erreur via `toast.error` en cas d'échec.

Quand la vidéo se charge (`onLoadedMetadata`), initialise le champ de fin à `video.duration` arrondi au dixième — sinon l'utilisateur doit le saisir alors qu'il ne veut couper que le début.

- [ ] **Step 3 : Ajouter l'action « Rogner » à la table des assets**

Dans `frontend/src/features/catalogue/AssetSection.tsx`, ajoute une prop optionnelle `onTrim?: (name: string) => void`. Quand elle est fournie, ajoute un `IconButton` avec l'icône `Scissors` de lucide-react, infobulle « Rogner le début et la fin », **avant** le bouton de suppression, dans la même `TableCell`.

`AssetSection` est partagé entre la section Sons et la section Clips : la prop reste absente pour les sons, dont les morceaux du label n'ont pas d'intro à retirer.

- [ ] **Step 4 : Brancher dans `Catalogue.tsx`**

Ajoute un état `trimming: string | null` et un état `trimJob: string | null`. Passe `onTrim={setTrimming}` à la section Clips uniquement, monte `<TrimDialog name={trimming} onClose={() => setTrimming(null)} onStarted={setTrimJob} />`, et affiche un `<JobLog jobId={trimJob} onDone={...} />` qui rafraîchit l'état et remet `trimJob` à `null` — le motif exact de `analyzeJobs` dans `ClipperTab.tsx`, qui existe précisément pour qu'un job terminé ne rejoue pas son `onDone`.

- [ ] **Step 5 : Vérifier le build**

```bash
cd frontend && npm run build
```

Attendu : aucune erreur TypeScript.

- [ ] **Step 6 : Commit**

```bash
git add frontend/src
git commit -m "feat(trim): modale de rognage dans le catalogue"
```

---

### Task 5 : Documentation

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: tout ce qui précède
- Produces: rien de code

- [ ] **Step 1 : Documenter dans `CLAUDE.md`**

Ajoute une entrée `trim_clip.py` à la section « Architecture », au format des autres (dense, chaque décision non évidente portant sa justification), et une mention de l'endpoint dans le paragraphe `webui.py`.

Elle doit couvrir, parce que ce sont les points qu'on ne redevine pas :

- **la coupe est destructive**, et pourquoi ce choix : mémoriser des bornes aurait obligé `load_clips`, le scan et son cache à les respecter, donc à toucher au cœur du montage pour une fonctionnalité de catalogue ;
- **le réencodage plutôt que la copie de flux** : les images-clés du catalogue sont irrégulières jusqu'à 5 s, mesuré — une copie laisserait plusieurs secondes d'intro ;
- **le bénéfice secondaire** : les clips AV1 deviennent du H.264, plus rapide à décoder pour le scan et le rendu, qui les redécodent à chaque génération ;
- **`crf` = 18 et non 20** : ce clip est un intermédiaire que le montage réencodera une seconde fois ;
- **le temporaire vit dans `clips/.trim/`**, un sous-dossier, parce que `load_clips` et `/api/state` parcourent `clips/` et prendraient un fichier voisin oublié pour un vrai clip ;
- **le remplacement n'a lieu qu'après un code retour nul**, donc une interruption laisse le clip intact ;
- **le nom de fichier ne change pas**, donc les sélections des niches survivent ;
- **le cache de scan se périme tout seul** par la date de modification — ne pas ajouter d'invalidation manuelle ;
- **`VIDEO_EXTS` et non `CLIP_EXTS`** côté endpoint : rogner une image n'a pas de sens ;
- **le job est nommé par clip** (`trim-<nom>`), pour autoriser deux rognages en parallèle tout en bloquant le double-clic sur le même clip.

- [ ] **Step 2 : Vérifier**

```bash
uv run pytest -q && cd frontend && npm run build
```

- [ ] **Step 3 : Commit**

```bash
git add CLAUDE.md
git commit -m "docs(trim): documente le rognage des clips du catalogue"
```

---

## Ce qui n'est pas dans ce plan

Rappel de la spec, pour qu'aucune tâche ne dérive :

- détection automatique d'intro ou d'outro ;
- annulation d'un rognage (le clip est réimportable depuis `clip_links.txt`) ;
- coupe au milieu d'un clip — seuls le début et la fin sont rognés ;
- rognage des sons de `tracks/` ou des sources du clipper (`data/clipper/`).
