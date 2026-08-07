"""clipper — vidéo longue parlée → shorts 9:16 classés par pertinence.

Second front de l'usine, indépendant du montage beatsync : on transcrit, on
laisse un LLM proposer les moments, on recale les bornes par du code pur, on
note, on classe, et on rend des shorts recadrés et sous-titrés.

Découpage volontaire : la logique pure au centre (testable sans FFmpeg, sans
Whisper, sans LLM), et trois fonctions d'I/O en périphérie — `transcribe`,
`track_faces`, `render_clip` — plus `_call_json`. Les tests ne touchent que le
centre.
"""

import json
import os
import subprocess
import urllib.request
from pathlib import Path

# Format de sortie du lot 1 : vertical en dur, pas de preset de format.
OUT_W, OUT_H = 1080, 1920

DEFAULTS = {
    "whisper_model": "small",   # taille du modèle faster-whisper
    "clip_count": 8,            # nombre de shorts gardés par source
    "min_dur": 15.0,            # s : en dessous, un extrait n'a pas d'histoire
    "max_dur": 60.0,            # s : au-delà, ce n'est plus un short
}

# Un blanc de cette longueur vaut une fin de phrase, même sans ponctuation :
# Whisper ponctue mal le français parlé, et le silence est un signal plus fiable.
SENTENCE_GAP = 0.6
# Respiration ajoutée de part et d'autre du clip. Attaquer pile sur l'attaque du
# premier mot s'entend comme une coupure ; 0,15 s suffit à l'adoucir sans laisser
# de blanc mort.
PAD = 0.15
_SENTENCE_END = (".", "!", "?", "…", ":")


def sentences(words: list[dict]) -> list[tuple[int, int]]:
    """Indices (premier, dernier) de chaque phrase. Une phrase se termine sur une
    ponctuation forte OU sur un silence d'au moins SENTENCE_GAP. Pure."""
    if not words:
        return []
    groups, start = [], 0
    for i, word in enumerate(words):
        last = i == len(words) - 1
        ends_on_punct = word["word"].rstrip().endswith(_SENTENCE_END)
        gap = (words[i + 1]["start"] - word["end"]) if not last else 0.0
        if last or ends_on_punct or gap >= SENTENCE_GAP:
            groups.append((start, i))
            start = i + 1
    return groups


def snap_to_speech(start: float, end: float, words: list[dict],
                   min_dur: float, max_dur: float) -> tuple[float, float] | None:
    """Recale un candidat du LLM sur des frontières de phrase, et le fait entrer
    dans [min_dur, max_dur]. Retourne None si c'est impossible — mieux vaut un
    clip de moins qu'un clip qui commence au milieu d'un mot. Pure."""
    groups = sentences(words)
    if not groups:
        return None

    # Phrase de départ : celle qui contient `start`, sinon la suivante ; à
    # défaut (timestamp au-delà de la fin) la dernière.
    first = next((g for g, (_, j) in enumerate(groups) if words[j]["end"] > start),
                 len(groups) - 1)
    # Phrase de fin : celle qui contient `end`, sinon la précédente.
    last = next((g for g in range(len(groups) - 1, -1, -1)
                 if words[groups[g][0]]["start"] < end), first)
    last = max(last, first)

    def span(a: int, b: int) -> tuple[float, float]:
        return words[groups[a][0]]["start"], words[groups[b][1]]["end"]

    # Trop long : on retire des phrases par la fin (le début porte le hook, il
    # ne se sacrifie jamais).
    while last > first and (span(first, last)[1] - span(first, last)[0]) > max_dur:
        last -= 1
    # Trop court : on absorbe la phrase suivante, tant qu'on reste sous max_dur.
    while last + 1 < len(groups):
        s, e = span(first, last)
        if e - s >= min_dur:
            break
        nxt = span(first, last + 1)
        if nxt[1] - nxt[0] > max_dur:
            break
        last += 1

    s, e = span(first, last)
    if not (min_dur <= e - s <= max_dur):
        return None

    # Respiration, plafonnée à la moitié du silence disponible pour ne jamais
    # mordre sur la phrase voisine.
    before = s - words[groups[first - 1][1]]["end"] if first > 0 else PAD * 2
    after = (words[groups[last + 1][0]]["start"] - e
             if last + 1 < len(groups) else PAD * 2)
    return (max(0.0, s - min(PAD, before / 2)), e + min(PAD, after / 2))


# Le hook pèse le plus : c'est la seule des trois notes qui décide du scroll.
# Les deux autres ne jouent qu'une fois le spectateur retenu.
WEIGHTS = {"hook": 0.4, "flow": 0.3, "value": 0.3}
# Au-delà de cette part du plus court des deux, deux candidats décrivent le même
# moment — on ne garde que le mieux noté.
OVERLAP_MAX = 0.5


def moment_score(moment: dict) -> float:
    """Note agrégée 0-100. Une note absente ou None vaut 0 : un échec LLM fait
    tomber le moment en fin de liste, il ne fait pas planter le classement."""
    return sum(w * float(moment.get(k) or 0) for k, w in WEIGHTS.items())


def _overlap_ratio(a: dict, b: dict) -> float:
    """Part du plus court des deux moments couverte par leur intersection."""
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    if inter <= 0:
        return 0.0
    shortest = min(a["end"] - a["start"], b["end"] - b["start"])
    return inter / shortest if shortest > 0 else 0.0


def rank_moments(moments: list[dict], count: int) -> list[dict]:
    """Top `count` moments, score décroissant, sans doublons de position.
    Ne mute pas l'entrée. Pure."""
    scored = [{**mo, "score": moment_score(mo)} for mo in moments]
    # `start` en second critère : deux ex æquo gardent un ordre stable, sans
    # quoi deux lancements sur la même source ne donneraient pas les mêmes clips.
    scored.sort(key=lambda mo: (-mo["score"], mo["start"]))
    kept: list[dict] = []
    for moment in scored:
        if len(kept) >= count:
            break
        if any(_overlap_ratio(moment, k) > OVERLAP_MAX for k in kept):
            continue
        kept.append(moment)
    return kept


# Le cadre ne suit le visage que s'il s'en écarte de plus de cette fraction de
# la LARGEUR DU CROP (donc de la largeur de l'image finale, puisque le crop est
# étiré vers OUT_W). Sans zone morte, le crop corrige en permanence des
# micro-déplacements et le plan « respire » — c'est ce qui donne le mal de mer.
# L'unité compte : `smooth_track` reçoit des pixels SOURCE, et une zone morte
# exprimée en pixels de sortie vaudrait 21 % de l'image en 720p contre 7 % en
# 4K — le même invité se cadrerait différemment selon la définition du rush.
DEAD_ZONE = 0.08
# Fenêtre (impaire) de la moyenne glissante : à 2 fps, 5 échantillons lissent sur
# 2,5 s, assez pour écraser une fausse détection sans traîner sur un vrai
# changement de cadre.
SMOOTH_WINDOW = 5
# Plafond de paliers dans l'expression de crop. Au-delà, l'expression devient
# illisible et son évaluation par frame commence à coûter.
MAX_STEPS = 120


def _fill_holes(centers: list[float | None], default: float) -> list[float]:
    """Trous intérieurs interpolés, trous de bord tenus à la valeur connue la
    plus proche. Aucune détection du tout → `default` partout."""
    known = [i for i, c in enumerate(centers) if c is not None]
    if not known:
        return [default] * len(centers)
    out: list[float] = []
    for i in range(len(centers)):
        if centers[i] is not None:
            out.append(float(centers[i]))
            continue
        before = [k for k in known if k < i]
        after = [k for k in known if k > i]
        if not before:
            out.append(float(centers[after[0]]))
        elif not after:
            out.append(float(centers[before[-1]]))
        else:
            a, b = before[-1], after[0]
            t = (i - a) / (b - a)
            out.append(float(centers[a]) + t * (float(centers[b]) - float(centers[a])))
    return out


def smooth_track(centers: list[float | None], default: float,
                 dead_zone: float) -> list[float]:
    """Trajectoire de suivi exploitable : trous comblés, moyenne glissante, zone
    morte. Pure."""
    if not centers:
        return []
    filled = _fill_holes(centers, default)
    half = SMOOTH_WINDOW // 2
    averaged = [
        sum(filled[max(0, i - half):i + half + 1])
        / len(filled[max(0, i - half):i + half + 1])
        for i in range(len(filled))
    ]
    out = [averaged[0]]
    for value in averaged[1:]:
        out.append(out[-1] if abs(value - out[-1]) < dead_zone else value)
    return out


def _even(value: float) -> int:
    """Arrondi à l'entier pair inférieur : une dimension ou un offset impair
    produit des artefacts de chroma en yuv420p."""
    return int(value) - (int(value) % 2)


def crop_size(src_w: int, src_h: int) -> tuple[int, int]:
    """Rectangle 9:16 le plus large qui tienne dans la source. Une source déjà
    plus verticale que 9:16 n'est pas recadrée en largeur. Pure."""
    width = min(src_w, _even(src_h * OUT_W / OUT_H))
    return (_even(width), src_h)


def _ramp(t0: float, x0: int, t1: float, x1: int) -> str:
    """Rampe linéaire de (t0, x0) à (t1, x1), en expression FFmpeg.

    Un palier sec ne « lisse » rien : le cadre reste figé le temps que la zone
    morte cède, puis saute de plusieurs pour cent de la largeur en une frame.
    C'est un saut de cadrage, pas un panoramique — d'où l'interpolation.
    `2*floor(…/2)` garde x pair, exigence du chroma yuv420p, au prix d'un
    escalier de 2 px invisible."""
    if x0 == x1:
        return str(x0)
    slope = (x1 - x0) / (t1 - t0)
    elapsed = "t" if t0 == 0 else f"(t-{t0:g})"
    return f"2*floor(({x0}+{slope:g}*{elapsed})/2)"


def crop_expr(track: list[float], sample_fps: float, crop_w: int,
              src_w: int) -> str:
    """Compile la trajectoire en expression FFmpeg pour `crop=x=…:eval=frame`,
    en interpolant linéairement entre les points de la trajectoire. Une
    trajectoire immobile rend un simple nombre (pas d'évaluation par frame).
    Pure."""
    centre = _even(max(0, (src_w - crop_w) / 2))
    if not track:
        return str(centre)

    xs = [_even(min(max(0.0, c - crop_w / 2), max(0, src_w - crop_w)))
          for c in track]
    # Un point par seconde suffit : la zone morte a déjà supprimé tout ce qui
    # bouge plus vite, et un palier par frame ferait exploser l'expression.
    step = max(1, int(round(sample_fps)))
    keyed = [(i / sample_fps, xs[i]) for i in range(0, len(xs), step)]
    # Paliers consécutifs identiques fusionnés : c'est le cas courant (plan fixe).
    merged: list[tuple[float, int]] = []
    for time, x in keyed:
        if not merged or merged[-1][1] != x:
            merged.append((time, x))
    if len(merged) > MAX_STEPS:
        keep = max(1, len(merged) // MAX_STEPS + 1)
        merged = merged[::keep]
    if len(merged) == 1:
        return str(merged[0][1])

    # Emboîtement en partant de la fin : chaque segment interpole jusqu'au point
    # suivant ; après le dernier point, le cadre tient sa valeur.
    expr = str(merged[-1][1])
    for (time, x), (next_time, next_x) in zip(reversed(merged[:-1]),
                                              reversed(merged[1:])):
        expr = (f"if(lt(t,{next_time:g}),"
                f"{_ramp(time, x, next_time, next_x)},{expr})")
    return expr


# Rouge Dancing Dead #ff1e46. ASS code la couleur en BGR, pas en RGB : R=ff,
# G=1e, B=46 s'écrit &H461EFF&. Inverser donne du bleu, pas une erreur visible
# au test — d'où le commentaire.
ASS_HIGHLIGHT = "&H461EFF&"
ASS_BASE = "&HFFFFFF&"
# 4 mots tiennent sur une ligne en Impact 64 px sur 1080 de large, et c'est la
# fenêtre de lecture d'un spectateur qui scrolle.
ASS_WORDS_PER_LINE = 4


def ass_time(seconds: float) -> str:
    """Horodatage ASS : H:MM:SS.cc (centièmes, pas millièmes)."""
    seconds = max(0.0, seconds)
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    # Troncature et non arrondi : round() ferait passer 1,999 s à « .100 »,
    # trois chiffres là où ASS en attend deux, et le sous-titre disparaît.
    return f"{hours}:{minutes:02d}:{secs:02d}.{int((seconds % 1) * 100):02d}"


def _ass_escape(text: str) -> str:
    """Neutralise ce qui pilote le rendu ASS. Les accolades lancent un bloc
    d'override dans ASS : pas d'échappement portable sur toutes les versions
    de libass. On substitue plutôt — une transcription n'en contient à peu près
    jamais. L'antislash et le retour à la ligne restent traités."""
    return (text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
                .replace("\n", " "))


def build_ass(words: list[dict], start: float, end: float, *,
              y: float = 0.74, size: int = 64) -> str:
    """Sous-titres karaoké : la ligne entière s'affiche, le mot en cours d'être
    prononcé passe en rouge. Temps rebasés sur `start`. Pure."""
    inside = [x for x in words if x["end"] > start and x["start"] < end]
    margin = int(round((1 - y) * OUT_H))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {OUT_W}\nPlayResY: {OUT_H}\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour,"
        " BackColour, Bold, BorderStyle, Outline, Shadow, Alignment,"
        " MarginL, MarginR, MarginV, Encoding\n"
        f"Style: DD,Impact,{size},{ASS_BASE},&H000000&,&H000000&,-1,1,4,0,2,"
        f"60,60,{margin},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR,"
        " MarginV, Effect, Text\n"
    )
    lines = []
    for i in range(0, len(inside), ASS_WORDS_PER_LINE):
        group = inside[i:i + ASS_WORDS_PER_LINE]
        texts = [_ass_escape(x["word"].strip()) for x in group]
        for j, word in enumerate(group):
            rendered = " ".join(
                f"{{\\c{ASS_HIGHLIGHT}}}{t}{{\\c{ASS_BASE}}}" if k == j else t
                for k, t in enumerate(texts))
            lines.append(
                f"Dialogue: 0,{ass_time(word['start'] - start)},"
                f"{ass_time(word['end'] - start)},DD,,0,0,0,,{rendered}")
    return header + "\n".join(lines) + ("\n" if lines else "")


def ffmpeg_path(path) -> str:
    """Chemin utilisable À L'INTÉRIEUR d'une chaîne de filtre ffmpeg. Le filtre
    s'écrit subtitles='<chemin>' : une apostrophe refermerait la chaîne, brisée
    le parseur. On normalise en `/`, échappe les `:` de lettres de lecteur, puis
    échappe les apostrophes (dernier, sinon le `\\` qu'on introduit serait mangé
    par le remplacement des séparateurs)."""
    s = str(path).replace("\\", "/").replace(":", "\\:")
    return s.replace("'", "'\\''")


# Longueur max d'une justification stockée. Le texte vient du LLM, donc d'une
# source non fiable : on le borne avant la base, pas à l'affichage.
WHY_MAX = 240
# Un modèle local a une fenêtre finie ; tronquer proprement vaut mieux que se
# faire couper la réponse au milieu d'un JSON.
DIGEST_MAX_CHARS = 40_000

_PROPOSE_SYSTEM = (
    "Tu es monteur pour un label de musique électronique. On te donne la "
    "transcription horodatée d'une vidéo longue. Tu repères les moments qui "
    "fonctionneraient seuls, sortis de leur contexte, en short vertical de "
    "15 à 60 secondes : une idée qui se tient du début à la fin, avec une "
    "accroche dans les premières secondes. Tu réponds en français."
)
_SCORE_SYSTEM = (
    "Tu notes un extrait vidéo court destiné à TikTok, sur trois critères, de "
    "0 à 100 : hook (les trois premières secondes retiennent-elles quelqu'un "
    "qui scrolle), flow (l'extrait se tient-il seul, du début à une fin "
    "satisfaisante), value (apporte-t-il quelque chose — une info, une "
    "émotion, une opinion tranchée). Tu es sévère : 50 est un extrait moyen. "
    "Tu justifies en une seule phrase, en français."
)

_PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {"moments": {"type": "array", "items": {
        "type": "object",
        "properties": {"start": {"type": "number"}, "end": {"type": "number"},
                       "title": {"type": "string"}},
        "required": ["start", "end", "title"]}}},
    "required": ["moments"],
}
_SCORE_SCHEMA = {
    "type": "object",
    "properties": {"hook": {"type": "integer"}, "flow": {"type": "integer"},
                   "value": {"type": "integer"}, "why": {"type": "string"}},
    "required": ["hook", "flow", "value", "why"],
}


def _call_json(system: str, user: str, schema: dict, seed: int, name: str) -> dict:
    """Un appel LLM rendant du JSON conforme à `schema`. Isolé pour être mocké.

    N'utilise PAS beatsync._call_llm : celui-ci a le prompt punchline codé en
    dur et une signature sans schéma. On réutilise en revanche son choix de
    backend et son chargement de .env, pour que LLM_BACKEND pilote les deux
    sous-systèmes de la même façon."""
    from beatsync import _llm_backend, _load_dotenv

    _load_dotenv()
    if _llm_backend() == "anthropic":
        import anthropic

        # L'API Messages n'expose pas de paramètre `seed` : on l'injecte dans le
        # texte du prompt pour la reproductibilité, comme le fait _punchline_user_prompt.
        user_with_seed = user + f"\n\n(variation n°{seed})"
        resp = anthropic.Anthropic(timeout=300).messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
            max_tokens=4096, system=system,
            messages=[{"role": "user", "content": user_with_seed}],
            output_config={"format": {"type": "json_schema", "schema": schema}})
        return json.loads(next(b.text for b in resp.content if b.type == "text"))

    base = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1").rstrip("/")
    body = {
        "model": os.environ.get("LMSTUDIO_MODEL", "local-model"),
        "messages": [
            {"role": "system",
             "content": system + " Réponds UNIQUEMENT en JSON conforme au schéma."},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,   # plus bas que les punchlines : on veut du jugement, pas de la créativité
        "seed": seed,         # reproductibilité, comme pour les punchlines
        "response_format": {"type": "json_schema", "json_schema": {
            "name": name, "strict": True, "schema": schema}},
    }
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    # 300 s : un transcript d'une heure fait travailler un modèle local longtemps.
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    return json.loads(data["choices"][0]["message"]["content"])


def transcript_digest(words: list[dict], max_chars: int = DIGEST_MAX_CHARS) -> str:
    """Transcript compacté en lignes `[mm:ss] phrase`, pour le prompt. Pure."""
    out: list[str] = []
    total = 0
    for first, last in sentences(words):
        start = words[first]["start"]
        text = " ".join(x["word"].strip() for x in words[first:last + 1])
        line = f"[{int(start) // 60:02d}:{int(start) % 60:02d}] {text}"
        if total + len(line) > max_chars:
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)


def moment_text(words: list[dict], start: float, end: float) -> str:
    """Texte prononcé dans une fenêtre. Pure."""
    return " ".join(x["word"].strip() for x in words
                    if x["end"] > start and x["start"] < end)


def propose_moments(words: list[dict], count: int, seed: int) -> list[dict]:
    """Candidats bruts du LLM. Dégrade en [] si le LLM échoue — l'usine ne
    bloque jamais dessus, exactement comme generate_punchlines."""
    user = (f"Propose {count} moments. Transcription :\n\n"
            + transcript_digest(words)
            + "\n\nRends start et end en SECONDES depuis le début de la vidéo.")
    try:
        data = _call_json(_PROPOSE_SYSTEM, user, _PROPOSE_SCHEMA, seed, "moments")
        # Un modèle local rend parfois une racine qui n'est pas l'objet demandé
        # (null, liste, nombre) : `strict: True` n'est pas honoré par tous.
        # L'usine dégrade plutôt que de tomber.
        raw_moments = data["moments"]
    except Exception:
        return []
    moments = []
    for raw in raw_moments:
        try:
            start, end = float(raw["start"]), float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue   # entrée malformée : on la jette, la source continue
        if end <= start:
            continue
        moments.append({"start": start, "end": end,
                        "title": str(raw.get("title", ""))[:WHY_MAX]})
    return moments


def score_moment(text: str, title: str, seed: int) -> dict:
    """Notes hook/flow/value d'un candidat. Dégrade en zéros si le LLM échoue :
    le moment tombe en fin de classement, il ne fait pas échouer la source."""
    user = f"Titre proposé : {title}\n\nTranscription de l'extrait :\n{text}"
    try:
        data = _call_json(_SCORE_SYSTEM, user, _SCORE_SCHEMA, seed, "score")
        # Un modèle local rend parfois une racine qui n'est pas l'objet demandé :
        # l'usine dégrade plutôt que de tomber.
        if not isinstance(data, dict):
            raise ValueError("racine non-objet")
    except Exception:
        return {"hook": 0, "flow": 0, "value": 0, "why": ""}

    def note(key: str) -> int:
        try:
            return max(0, min(100, int(data.get(key, 0))))
        except (TypeError, ValueError):
            return 0

    return {"hook": note("hook"), "flow": note("flow"), "value": note("value"),
            "why": str(data.get("why", ""))[:WHY_MAX]}


# --- I/O : transcription, détection de visage, rendu ------------------------------

# 2 fps pour le suivi de visage : même cadence que le scan de beatsync. Plus fin
# ne sert à rien (la moyenne glissante et la zone morte écrasent tout ce qui
# bouge plus vite), et le décodage domine le coût.
SAMPLE_FPS = 2.0


def transcribe(video_path: Path, model: str = "small") -> list[dict]:
    """Mots horodatés de la bande son. Langue auto-détectée. I/O.

    Une source sans piste audio (upload muet, import vidéo sans son) fait
    planter le décodeur de faster-whisper avec une IndexError opaque : on le
    vérifie nous-mêmes et on dégrade en liste vide, comme un LLM qui échoue —
    l'orchestrateur sait déjà traiter un transcript vide."""
    if not has_audio(video_path):
        return []

    from faster_whisper import WhisperModel  # import paresseux : ~1 s + le modèle

    whisper = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _ = whisper.transcribe(str(video_path), word_timestamps=True,
                                     vad_filter=True)
    words = []
    for segment in segments:
        for word in (segment.words or []):
            words.append({"word": word.word.strip(),
                          "start": float(word.start), "end": float(word.end)})
    return words


def _ffprobe(video_path: Path, entries: str) -> list[str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", entries, "-of", "csv=p=0:s=,", str(video_path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return out.split(",")


def probe_size(video_path: Path) -> tuple[int, int]:
    """Dimensions de la piste vidéo. I/O."""
    width, height = _ffprobe(video_path, "stream=width,height")[:2]
    return int(width), int(height)


def probe_duration(video_path: Path) -> float:
    """Durée en secondes. I/O."""
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True, check=True).stdout.strip())


def has_audio(video_path: Path) -> bool:
    """La source a-t-elle au moins une piste audio ? I/O.

    À appeler avant transcribe : sans ça, une source muette (fréquente sur un
    upload utilisateur ou un import vidéo) fait planter le décodeur audio de
    faster-whisper plutôt que de dégrader proprement."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return bool(out)


# Échecs de lecture consécutifs tolérés avant de conclure à une fin de flux.
# Sur un conteneur sans index propre (upload utilisateur, vidéo VFR), un seek
# isolé peut rater sans que le flux soit fini : sortir dès le premier échec
# tronque le suivi et fige le cadrage pour le reste du short, en silence.
_MAX_CONSECUTIVE_READ_FAILURES = 5


def track_faces(video_path: Path, start: float, end: float) -> list[float | None]:
    """Centre horizontal (px, dans le repère de la source) du plus grand visage,
    échantillonné à SAMPLE_FPS. None quand aucun visage n'est détecté (ou quand
    la lecture échoue ponctuellement — smooth_track sait combler ces trous). I/O."""
    import cv2  # import paresseux : coûteux, inutile à la logique pure

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    capture = cv2.VideoCapture(str(video_path))
    centers: list[float | None] = []
    try:
        time = start
        consecutive_failures = 0
        while time < end:
            capture.set(cv2.CAP_PROP_POS_MSEC, time * 1000)
            ok, frame = capture.read()
            if not ok:
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_READ_FAILURES:
                    break
                centers.append(None)
                time += 1 / SAMPLE_FPS
                continue
            consecutive_failures = 0
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.15, 5, minSize=(60, 60))
            if len(faces) == 0:
                centers.append(None)
            else:
                # Le plus grand visage : sur un plan à plusieurs personnes,
                # c'est presque toujours celui au premier plan, donc l'orateur.
                x, _, w, _ = max(faces, key=lambda f: f[2] * f[3])
                centers.append(float(x) + w / 2)
            time += 1 / SAMPLE_FPS
    finally:
        capture.release()
    return centers


def render_clip(video_path: Path, start: float, end: float, out_path: Path, *,
                words: list[dict], config: dict) -> None:
    """Rend un short : recadrage suivi, mise à l'échelle, sous-titres incrustés.
    Un seul appel ffmpeg. I/O."""
    src_w, src_h = probe_size(video_path)
    crop_w, crop_h = crop_size(src_w, src_h)
    # dead_zone en pixels SOURCE (track_faces travaille dans ce repère) : la
    # fraction se rapporte donc à crop_w, pas à OUT_W — le crop est ensuite
    # étiré vers OUT_W, si bien que DEAD_ZONE × crop_w vaut la même fraction de
    # l'image finale quelle que soit la définition de la source.
    track = smooth_track(track_faces(video_path, start, end),
                         default=src_w / 2, dead_zone=DEAD_ZONE * crop_w)
    expr = crop_expr(track, SAMPLE_FPS, crop_w, src_w)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = out_path.with_suffix(".ass")
    ass_path.write_text(build_ass(words, start, end), encoding="utf-8")

    # eval=frame seulement si l'expression dépend du temps : sinon on paie une
    # évaluation par frame pour une constante.
    evaluation = ":eval=frame" if "if(" in expr else ""
    filters = (f"crop={crop_w}:{crop_h}:x='{expr}':y=0{evaluation},"
               f"scale={OUT_W}:{OUT_H}:flags=lanczos,"
               f"subtitles='{ffmpeg_path(ass_path)}'")
    try:
        args = ["-y", "-loglevel", "error",
                "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(video_path),
                "-vf", filters, "-r", str(config.get("fps", 30)),
                "-c:v", "libx264", "-crf", str(config.get("crf", 20)),
                "-preset", config.get("preset", "medium"), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", config.get("audio_bitrate", "192k"),
                "-movflags", "+faststart",
                # Mêmes flags que beatsync : sans eux, l'encodeur date le fichier
                # et deux rendus identiques diffèrent octet à octet.
                "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
                str(out_path)]
        # Pas de check=True : un CalledProcessError n'expose que le code retour,
        # jamais le stderr de ffmpeg — or c'est ce message (filtre invalide,
        # codec manquant, chemin de sous-titres cassé) que l'utilisateur doit
        # voir dans le journal quand un rendu échoue. Même motif que
        # beatsync._run_ffmpeg.
        result = subprocess.run(["ffmpeg", *args], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg a échoué :\n  ffmpeg {' '.join(args)}\n"
                               f"{result.stderr}")
    finally:
        ass_path.unlink(missing_ok=True)
