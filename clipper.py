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
import urllib.error
import urllib.request
from pathlib import Path

# Tout le cadrage passe par le préfixe `speaker.` : un style mixte (certains
# noms importés, d'autres via le module) laissait traîner un `MAX_STEPS` importé
# et jamais utilisé, et rend illisible ce qui vient d'où. Le préfixe dit aussi
# clairement, à la lecture, quel module est le plus bas niveau des deux.
import speaker

DEFAULTS = {
    "whisper_model": "small",   # taille du modèle faster-whisper
    "clip_count": 8,            # nombre de shorts gardés par source
    "min_dur": 15.0,            # s : en dessous, un extrait n'a pas d'histoire
    "max_dur": 60.0,            # s : au-delà, ce n'est plus un short
    # Transcript envoyé en une fois au modèle. Le bon réglage dépend du contexte
    # du modèle chargé, que le code ne peut pas connaître : 6000 caractères font
    # ~2600 tokens (mesuré à ~2,3 caractères par token sur du français horodaté),
    # ce qui laisse de la marge sur un modèle local chargé à 4096.
    "digest_chars": 6000,
    # Recadrage sur celui qui parle, avec coupes franches. Désactivable : si la
    # détection se comporte mal sur un contenu donné, on doit pouvoir revenir au
    # suivi simple sans attendre un correctif.
    "speaker_cuts": True,
    # Durée minimale d'un plan, en secondes. En dessous le cadre clignote, au
    # delà il reste sur quelqu'un qui ne parle plus.
    "min_shot": 1.2,
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


# Rouge Dancing Dead #ff1e46. ASS code la couleur en BGR, pas en RGB : R=ff,
# G=1e, B=46 s'écrit &H461EFF&. Inverser donne du bleu, pas une erreur visible
# au test — d'où le commentaire.
ASS_HIGHLIGHT = "&H461EFF&"
ASS_BASE = "&HFFFFFF&"
# 4 mots tiennent sur une ligne en Impact 64 px sur 1080 de large, et c'est la
# fenêtre de lecture d'un spectateur qui scrolle.
ASS_WORDS_PER_LINE = 4
# Le projet EMBARQUE ses polices (beatsync.FONTS_DIR) : « Impact » n'est pas
# installable partout et libass lui substituerait silencieusement une sans-serif
# quelconque. Anton est le substitut sous licence OFL qu'utilise déjà beatsync
# pour le nom logique « impact » — nommer autre chose ici donnerait deux typos
# différentes à des vidéos censées venir du même label.
ASS_FONT = "Anton"


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
    margin = int(round((1 - y) * speaker.OUT_H))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {speaker.OUT_W}\nPlayResY: {speaker.OUT_H}\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour,"
        " BackColour, Bold, BorderStyle, Outline, Shadow, Alignment,"
        " MarginL, MarginR, MarginV, Encoding\n"
        f"Style: DD,{ASS_FONT},{size},{ASS_BASE},&H000000&,&H000000&,-1,1,4,0,2,"
        f"60,60,{margin},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR,"
        " MarginV, Effect, Text\n"
    )
    lines = []
    groups = [inside[i:i + ASS_WORDS_PER_LINE]
              for i in range(0, len(inside), ASS_WORDS_PER_LINE)]
    for g, group in enumerate(groups):
        texts = [_ass_escape(x["word"].strip()) for x in group]
        for j, word in enumerate(group):
            rendered = " ".join(
                f"{{\\c{ASS_HIGHLIGHT}}}{t}{{\\c{ASS_BASE}}}" if k == j else t
                for k, t in enumerate(texts))
            # La ligne tient jusqu'au mot suivant, pas jusqu'à la fin du mot
            # courant : borner sur word["end"] éteint le sous-titre pendant
            # chaque respiration, et le français parlé en est plein — la ligne
            # clignotait en continu. Pour le dernier mot d'un groupe, le mot
            # suivant est le premier du groupe d'après (même règle, aucun
            # chevauchement : l'événement précédent finit où le suivant
            # commence) ; seul le tout dernier mot du clip garde sa propre fin.
            if j + 1 < len(group):
                until = group[j + 1]["start"]
            elif g + 1 < len(groups):
                until = groups[g + 1][0]["start"]
            else:
                until = group[-1]["end"]
            lines.append(
                f"Dialogue: 0,{ass_time(word['start'] - start)},"
                f"{ass_time(until - start)},DD,,0,0,0,,{rendered}")
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

def _propose_system(min_dur: float, max_dur: float) -> str:
    """Prompt système de la proposition. Les bornes de durée y sont INTERPOLÉES,
    pas codées en dur : `min_dur`/`max_dur` sont réglables, et un prompt qui
    annonce « 15 à 60 secondes » à quelqu'un réglé sur 20–45 s ment au modèle."""
    return (
        "Tu es monteur pour un label de musique électronique. On te donne la "
        "transcription horodatée d'une vidéo longue. Tu repères les moments qui "
        "fonctionneraient seuls, sortis de leur contexte, en short vertical de "
        f"{min_dur:g} à {max_dur:g} secondes : une idée qui se tient du début à "
        "la fin, avec une accroche dans les premières secondes. Tu réponds en "
        "français."
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


def _json_anthropic(system: str, user: str, schema: dict, seed: int,
                    name: str) -> dict:
    """Appel JSON structuré via l'API Anthropic."""
    import anthropic

    # L'API Messages n'expose pas de paramètre `seed` : on l'injecte dans le
    # texte du prompt pour la reproductibilité, comme le fait _punchline_user_prompt.
    user_with_seed = user + f"\n\n(variation n°{seed})"
    # Les erreurs du SDK (clé absente, quota, surcharge) remontent telles quelles :
    # leur message dit déjà ce qui ne va pas, l'emballer le rendrait plus opaque.
    resp = anthropic.Anthropic(timeout=300).messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
        max_tokens=4096, system=system,
        messages=[{"role": "user", "content": user_with_seed}],
        output_config={"format": {"type": "json_schema", "schema": schema}})
    try:
        text = next(b.text for b in resp.content if b.type == "text")
    except StopIteration:
        # Réponse sans bloc texte (refus, arrêt sur max_tokens) : un StopIteration
        # nu ne dirait rien de ce que le modèle a réellement rendu.
        raise RuntimeError(
            f"réponse Anthropic sans texte : {str(resp.content)[:500]}") from None
    return _loads_or_explain(text, "contenu rendu par Anthropic")


def _json_lmstudio(system: str, user: str, schema: dict, seed: int,
                   name: str) -> dict:
    """Appel JSON structuré via un serveur local compatible OpenAI (LM Studio)."""
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
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
        data = _loads_or_explain(body, "réponse LM Studio")
    except urllib.error.HTTPError as exc:
        # LM Studio met son explication dans le CORPS de la réponse, pas dans le
        # statut : un dépassement de contexte sort en 400 en disant combien de
        # tokens le prompt fait et combien le modèle accepte. Sans le corps,
        # l'utilisateur ne voit qu'un « HTTP Error 400 » qui n'aide personne.
        raise RuntimeError(
            f"LM Studio a répondu HTTP {exc.code} : "
            f"{_response_excerpt(exc)}") from exc
    # Un chemin d'endpoint erroné (/chat/completions au lieu de
    # /v1/chat/completions) rend un 200 portant {"error": ...} : sans ce test, la
    # ligne suivante lève un KeyError nu et le message du serveur est perdu.
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"LM Studio : {_error_message(data['error'])}")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            f"réponse LM Studio inexploitable : {str(data)[:500]}") from exc
    return _loads_or_explain(content, "contenu rendu par LM Studio")


def _loads_or_explain(payload, what: str) -> dict:
    """`json.loads` dont l'échec DIT ce qui a été reçu.

    Le cas est fréquent avec un modèle local qui n'honore pas `strict: True` et
    rend de la prose au lieu du JSON : une JSONDecodeError nue affiche
    « Expecting value: line 1 column 1 » sans un caractère du corps, et
    l'utilisateur n'a aucun moyen de savoir que son modèle a répondu en
    français."""
    try:
        return json.loads(payload)
    except (ValueError, TypeError) as exc:   # JSONDecodeError hérite de ValueError
        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) \
            else str(payload)
        raise RuntimeError(f"{what} : JSON illisible ({exc}) — "
                           f"{text[:500]!r}") from exc


def _response_excerpt(exc: "urllib.error.HTTPError") -> str:
    """Corps d'une réponse HTTP en erreur, tronqué. Un corps illisible ne doit
    pas masquer le code HTTP, seule information qui reste alors."""
    try:
        return exc.read().decode("utf-8", "replace")[:500]
    except Exception:
        return "(corps illisible)"


def _error_message(error) -> str:
    """Message d'une erreur LM Studio : tantôt une chaîne, tantôt un objet à la
    mode OpenAI portant `message`."""
    if isinstance(error, dict):
        return str(error.get("message") or error)[:500]
    return str(error)[:500]


# Nom de fonction par backend, résolu via globals() au moment de l'appel pour
# rester monkeypatchable dans les tests — même convention que beatsync.
_JSON_BACKENDS = {"anthropic": "_json_anthropic", "lmstudio": "_json_lmstudio"}


def _call_json(system: str, user: str, schema: dict, seed: int, name: str) -> dict:
    """Un appel LLM rendant du JSON conforme à `schema`. Isolé pour être mocké.

    N'utilise PAS beatsync._call_llm : celui-ci a le prompt punchline codé en
    dur et une signature sans schéma. On réutilise en revanche son choix de
    backend et son chargement de .env, et on honore comme lui `LLM_FALLBACK` —
    sans quoi `LLM_BACKEND` ne piloterait pas les deux sous-systèmes de la même
    façon, et un LM Studio éteint ferait échouer le clipper alors que beatsync
    aurait basculé sur Anthropic."""
    from beatsync import _llm_backend, _load_dotenv

    _load_dotenv()
    primary = _llm_backend()
    order = [primary]
    fallback = os.environ.get("LLM_FALLBACK", "").strip().lower()
    if fallback and fallback != primary:
        order.append(fallback)
    last_exc: Exception | None = None
    for backend in order:
        fnname = _JSON_BACKENDS.get(backend)
        if fnname is None:
            continue
        try:
            return globals()[fnname](system, user, schema, seed, name)
        except Exception as exc:   # on tente le repli, sinon on remonte l'erreur
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"backend LLM inconnu : {primary!r}")


def _digest_line(words: list[dict], first: int, last: int) -> str:
    """Une phrase du digest : `[mm:ss] texte`. Pure."""
    start = words[first]["start"]
    text = " ".join(x["word"].strip() for x in words[first:last + 1])
    return f"[{int(start) // 60:02d}:{int(start) % 60:02d}] {text}"


def transcript_digest(words: list[dict], max_chars: int = DIGEST_MAX_CHARS) -> str:
    """Transcript compacté en lignes `[mm:ss] phrase`, pour le prompt. Pure.

    La PREMIÈRE phrase sort toujours, même si elle dépasse à elle seule
    `max_chars` : c'est le cas de la fenêtre « phrase géante » que
    `transcript_windows` isole exprès (un monologue sans silence ni ponctuation
    forte). Sans cette exception, le digest de cette fenêtre était vide et le
    prompt partait littéralement « Propose 2 moments. Transcription : (rien) » —
    le modèle inventait alors des timestamps, et le passage que la fenêtre
    devait sauver restait invisible. Mieux vaut un prompt trop long, qui échoue
    bruyamment sur le contexte du modèle, qu'un prompt vide qui hallucine."""
    out: list[str] = []
    total = 0
    for first, last in sentences(words):
        line = _digest_line(words, first, last)
        if out and total + len(line) > max_chars:
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)


def transcript_windows(words: list[dict], max_chars: int) -> list[list[dict]]:
    """Découpe les mots en fenêtres consécutives dont le digest tient dans
    `max_chars`, sans jamais couper une phrase. Pure.

    C'est ce qui remplace la troncature : sur une source d'une heure, le digest
    entier ferait ~82 000 caractères et tout ce qui dépassait la limite n'était
    jamais vu du modèle — la seconde moitié de l'épisode ignorée en silence."""
    windows: list[list[dict]] = []
    first_word: int | None = None
    last_word = 0
    size = 0
    for first, last in sentences(words):
        line = len(_digest_line(words, first, last)) + 1
        if first_word is not None and size + line > max_chars:
            windows.append(words[first_word:last_word + 1])
            first_word, size = None, 0
        if first_word is None:
            # Une phrase seule plus longue que max_chars forme sa propre
            # fenêtre : la jeter perdrait du transcript, ce qu'on corrige ici.
            first_word = first
        last_word = last
        size += line
    if first_word is not None:
        windows.append(words[first_word:last_word + 1])
    return windows


def moment_text(words: list[dict], start: float, end: float) -> str:
    """Texte prononcé dans une fenêtre. Pure."""
    return " ".join(x["word"].strip() for x in words
                    if x["end"] > start and x["start"] < end)


def propose_moments(words: list[dict], count: int, seed: int, *,
                    digest_chars: int = DEFAULTS["digest_chars"],
                    min_dur: float = DEFAULTS["min_dur"],
                    max_dur: float = DEFAULTS["max_dur"],
                    log=None) -> list[dict]:
    """Candidats bruts du LLM, demandés fenêtre par fenêtre. Dégrade en [] si
    tous les appels échouent — l'usine ne bloque jamais sur le LLM, exactement
    comme generate_punchlines — mais journalise alors la cause via `log`.

    Le transcript entier ne tient pas dans le contexte d'un modèle local : il est
    découpé en fenêtres (`transcript_windows`), chacune interrogée séparément.
    Les timestamps du digest étant absolus, les candidats se concatènent sans
    recalage. L'échec d'une fenêtre ne perd pas les autres."""
    windows = transcript_windows(words, digest_chars)
    if not windows:
        return []
    # Au moins deux candidats par fenêtre : avec un seul, un passage tardif de
    # l'épisode n'aurait qu'une proposition à opposer à tout le reste.
    per_window = max(2, -(-count // len(windows)))
    system = _propose_system(min_dur, max_dur)
    moments = []
    for i, window in enumerate(windows):
        if log and len(windows) > 1:
            log(f"  fenêtre {i + 1}/{len(windows)}…")
        # Les bornes de durée sont rappelées ici en plus du prompt système : le
        # modèle local de la tour propose des fenêtres d'une seconde quand elles
        # ne figurent que dans le système, visiblement trop loin de la question.
        user = (f"Propose {per_window} moments, chacun d'une durée comprise "
                f"entre {min_dur:g} et {max_dur:g} secondes. Transcription :\n\n"
                + transcript_digest(window, digest_chars)
                + "\n\nRends start et end en SECONDES depuis le début de la vidéo.")
        try:
            # Seed décalée par fenêtre : déterministe, mais deux fenêtres ne
            # doivent pas partager le même tirage.
            data = _call_json(system, user, _PROPOSE_SCHEMA, seed + i, "moments")
            # Un modèle local rend parfois une racine qui n'est pas l'objet
            # demandé (null, liste, nombre) : `strict: True` n'est pas honoré
            # par tous. L'usine dégrade plutôt que de tomber.
            raw_moments = data["moments"]
            # …et parfois `moments` n'est pas non plus une liste (un nombre,
            # null). L'itération est DANS le try : hors de lui, le TypeError
            # s'échappait et faisait tomber tout le traitement de la source —
            # une fenêtre mal formée perdait les treize autres.
            if not isinstance(raw_moments, list):
                raise TypeError(f"`moments` n'est pas une liste : "
                                f"{type(raw_moments).__name__}")
        except Exception as exc:
            if log:
                log(f"  fenêtre {i + 1}/{len(windows)} : échec du LLM ({exc})")
            continue
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


def score_moment(text: str, title: str, seed: int, *, log=None) -> dict:
    """Notes hook/flow/value d'un candidat. Dégrade en zéros si le LLM échoue :
    le moment tombe en fin de classement, il ne fait pas échouer la source. La
    cause part dans `log` — sans ça, un échec systématique ressemble à un
    classement médiocre."""
    user = f"Titre proposé : {title}\n\nTranscription de l'extrait :\n{text}"
    try:
        data = _call_json(_SCORE_SYSTEM, user, _SCORE_SCHEMA, seed, "score")
        # Un modèle local rend parfois une racine qui n'est pas l'objet demandé :
        # l'usine dégrade plutôt que de tomber.
        if not isinstance(data, dict):
            raise ValueError("racine non-objet")
    except Exception as exc:
        if log:
            log(f"  notation impossible ({exc}) : moment noté 0")
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


# Réponse de la sonde ci-dessous, mémorisée : c'est un sous-processus, on ne le
# paie pas une fois par segment rendu.
_CROP_EVAL_SUPPORTED: bool | None = None


def crop_supports_eval() -> bool:
    """Le filtre `crop` de la ffmpeg installée accepte-t-il l'option `eval` ? I/O.

    Question de VERSION, pas de mécanisme : jusqu'à ffmpeg 7, `crop` porte une
    option `eval` qui vaut `init` par défaut — sans `eval=frame`, une expression
    de `x` n'est évaluée qu'une fois et le cadre reste figé à sa position de
    départ (régression visuelle silencieuse). Depuis ffmpeg 8, l'option a
    disparu : `x`/`y` sont marquées runtime-tunable (`T`) et réévaluées par frame
    nativement, et passer `eval=frame` fait échouer TOUT le rendu avec
    « Option not found ». On sonde donc plutôt que de choisir un camp : la tour
    de production n'a pas forcément la même ffmpeg que la machine de dev.

    Sonde en échec (ffmpeg absent, sortie inattendue) = option absente, le
    comportement des versions récentes, qui ne casse rien sur elles."""
    global _CROP_EVAL_SUPPORTED
    if _CROP_EVAL_SUPPORTED is None:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=crop"],
                                 capture_output=True, text=True, timeout=30)
            # Une ligne d'option ffmpeg s'écrit « <nom> <type> <drapeaux> … » :
            # on cherche le nom exact en tête, pas « eval » n'importe où (les
            # descriptions parlent d'« evaluate »).
            _CROP_EVAL_SUPPORTED = any(
                line.split()[:1] == ["eval"]
                for line in (out.stdout + out.stderr).splitlines())
        except Exception:
            _CROP_EVAL_SUPPORTED = False
    return _CROP_EVAL_SUPPORTED


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
                words: list[dict], config: dict, log=None) -> None:
    """Rend un short : recadrage suivi, mise à l'échelle, sous-titres incrustés.
    Un seul appel ffmpeg. I/O.

    `log` est le journal du job : l'analyse du cadrage dure quelques secondes par
    clip et dégrade en cadrage centré quand la source est illisible — sans
    journal, ce repli est indiscernable d'une vidéo sans visage."""
    src_w, src_h = probe_size(video_path)
    crop_w, crop_h = speaker.crop_size(src_w, src_h)
    if config.get("speaker_cuts", True):
        segments = speaker.analyze_framing(
            video_path, start, end, src_w, src_h,
            min_shot=float(config.get("min_shot", speaker.MIN_SHOT)), log=log)
    else:
        # Repli : suivi lissé du plus grand visage, le comportement d'avant le
        # recadrage sur le locuteur. C'est la porte de sortie quand la détection
        # se comporte mal sur un contenu donné. dead_zone en pixels SOURCE
        # (track_faces travaille dans ce repère), donc rapportée à crop_w — le
        # crop est ensuite étiré vers OUT_W, si bien que DEAD_ZONE × crop_w vaut
        # la même fraction de l'image finale quelle que soit la définition.
        track = speaker.smooth_track(
            track_faces(video_path, start, end),
            default=src_w / 2, dead_zone=speaker.DEAD_ZONE * crop_w)
        segments = speaker.track_to_segments(track, SAMPLE_FPS, crop_w, src_w)
    expr = speaker.crop_expr(segments, crop_w, src_w)

    # Police EMBARQUÉE, comme beatsync : sans fontsdir, libass demande « Anton »
    # à fontconfig et lui substitue silencieusement une sans-serif quelconque
    # sur une machine où elle n'est pas installée (la tour de prod, typiquement).
    from beatsync import FONTS_DIR   # import paresseux : beatsync est lourd

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = out_path.with_suffix(".ass")
    ass_path.write_text(build_ass(words, start, end), encoding="utf-8")

    # `eval=frame` seulement si la ffmpeg installée connaît l'option (elle a
    # disparu en ffmpeg 8, où la passer fait échouer le rendu entier) ET si
    # l'expression dépend du temps (sinon on paie une évaluation par frame pour
    # une constante). Le test est délégué à `speaker.expr_needs_eval` : chercher
    # « if( » ne marche plus, un segment unique non constant compile en une
    # simple rampe sans condition — et sans `eval=frame` le cadre resterait figé,
    # en silence.
    evaluation = (":eval=frame"
                  if speaker.expr_needs_eval(expr) and crop_supports_eval()
                  else "")
    filters = (f"crop={crop_w}:{crop_h}:x='{expr}':y=0{evaluation},"
               f"scale={speaker.OUT_W}:{speaker.OUT_H}:flags=lanczos,"
               f"subtitles='{ffmpeg_path(ass_path)}'"
               f":fontsdir='{ffmpeg_path(FONTS_DIR)}'")
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
