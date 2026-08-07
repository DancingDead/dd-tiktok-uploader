"""clipper — vidéo longue parlée → shorts 9:16 classés par pertinence.

Second front de l'usine, indépendant du montage beatsync : on transcrit, on
laisse un LLM proposer les moments, on recale les bornes par du code pur, on
note, on classe, et on rend des shorts recadrés et sous-titrés.

Découpage volontaire : la logique pure au centre (testable sans FFmpeg, sans
Whisper, sans LLM), et trois fonctions d'I/O en périphérie — `transcribe`,
`track_faces`, `render_clip` — plus `_call_json`. Les tests ne touchent que le
centre.
"""

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
