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
# la largeur de sortie. Sans zone morte, le crop corrige en permanence des
# micro-déplacements et le plan « respire » — c'est ce qui donne le mal de mer.
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


def crop_expr(track: list[float], sample_fps: float, crop_w: int,
              src_w: int) -> str:
    """Compile la trajectoire en expression FFmpeg pour `crop=x=…:eval=frame`.
    Une trajectoire immobile rend un simple nombre (pas d'évaluation par frame).
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

    # Emboîtement en partant de la fin : chaque palier vaut jusqu'au suivant.
    expr = str(merged[-1][1])
    for (_, x), (next_time, _) in zip(reversed(merged[:-1]),
                                      reversed(merged[1:])):
        expr = f"if(lt(t,{next_time:g}),{x},{expr})"
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

        resp = anthropic.Anthropic().messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
            max_tokens=4096, system=system,
            messages=[{"role": "user", "content": user}],
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
    except Exception:
        return []
    moments = []
    for raw in data.get("moments", []):
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
    except Exception:
        return {"hook": 0, "flow": 0, "value": 0, "why": ""}

    def note(key: str) -> int:
        try:
            return max(0, min(100, int(data.get(key, 0))))
        except (TypeError, ValueError):
            return 0

    return {"hook": note("hook"), "flow": note("flow"), "value": note("value"),
            "why": str(data.get("why", ""))[:WHY_MAX]}
