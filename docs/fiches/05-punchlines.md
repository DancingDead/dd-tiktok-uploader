# 05 — Punchlines

> `assign_caption_slots` · `_call_llm` · `generate_punchlines` · `apply_subtitles` — lignes 1301-1491
> ← [04 build_edl](04-build-edl.md) · → [06 rendu](06-rendu.md)

## Ce que fait ce bloc

Poser un texte incrusté sur la vidéo. Deux modes :

| Mode | Source du texte | Appel LLM |
|---|---|---|
| `"llm"` (défaut) | généré par un modèle depuis un préprompt | oui |
| `"fixe"` | écrit à la main dans `subtitles.text` | non |

Le bloc s'insère **entre** `build_edl` et `render` : il annote l'EDL d'une clé
`caption` par segment. `render` se contente de la dessiner.

---

## `assign_caption_slots(edl, min_dur)` — l.1032 · **pure**

**Le problème** : pendant le strobo, on coupe à chaque beat. Une punchline par
segment changerait 4 fois par seconde. Illisible.

**La solution** : découpler le rythme du texte du rythme des coupes.

```python
slot = -1
slot_start = 0.0
for entry in edl:
    if slot < 0 or entry["timeline_start"] - slot_start >= min_dur - 1e-9:
        slot += 1
        slot_start = entry["timeline_start"]
    entry["caption_slot"] = slot
return slot + 1
```

Un **créneau** regroupe les segments consécutifs qui tiennent dans `min_dur`
(1,4 s par défaut). Tous partagent la même punchline.

### Le détail qui compte

Le texte change **à une coupe**, jamais au milieu d'un plan. On n'attend pas
exactement `min_dur` : on attend la première coupe **après** `min_dur`. Un
changement de texte en plein plan se verrait comme une erreur ; sur une coupe,
il passe pour du montage.

La fonction retourne le **nombre de créneaux** — c'est-à-dire exactement combien
de punchlines demander au LLM. Pas d'estimation, pas de surplus jeté.

---

## Le dispatch LLM

Trois couches, chacune avec une responsabilité :

```
generate_punchlines   ← cache + dégradation en []
  └─ _call_llm        ← choix du backend + repli
       ├─ _call_lmstudio    (local, HTTP stdlib)
       └─ _call_anthropic   (SDK Anthropic)
```

### `_call_llm(preprompt, count, seed, model)` — l.1149

```python
primary = _llm_backend()                       # LLM_BACKEND, défaut "lmstudio"
order = [primary]
fallback = os.environ.get("LLM_FALLBACK", "").strip().lower()
if fallback and fallback != primary:
    order.append(fallback)
for name in order:
    fnname = _LLM_BACKENDS.get(name)
    if fnname is None:
        continue
    try:
        return globals()[fnname](preprompt, count, seed, model)
    except Exception as exc:
        last_exc = exc
```

### Pourquoi `globals()[fnname]` et pas la fonction directement

```python
_LLM_BACKENDS = {"anthropic": "_call_anthropic", "lmstudio": "_call_lmstudio"}
```

Le dict stocke des **noms**, résolus au moment de l'appel. Si on stockait les
objets fonction, un `monkeypatch.setattr(beatsync, "_call_lmstudio", fake)` dans
un test ne changerait rien : le dict garderait la référence d'origine, capturée
à l'import.

C'est du code écrit pour être testable, et le commentaire l.1144-1145 le dit.

### Pourquoi LM Studio par défaut

Coût nul. L'usine génère des lots de 10, 20, 50 vidéos ; chacune consomme un
appel LLM. Un modèle local tourne sur la tour de prod sans facturer.

`LLM_FALLBACK=anthropic` permet de basculer sur Claude quand le serveur local
est éteint.

### `_call_lmstudio` — l.1369

HTTP via `urllib` de la stdlib, pas de dépendance ajoutée pour un POST JSON.

```python
"temperature": 0.8,
"seed": seed,
"response_format": {"type": "json_schema", ...}
```

Le `seed` est transmis au serveur — reproductibilité jusque dans le LLM.

Note de compatibilité (l.1119) : **LM Studio ≥ 0.4 exige `json_schema`** ;
l'ancien `json_object` renvoie 400.

### `_call_anthropic` — l.1346

SDK officiel, `output_config.format` avec un JSON Schema. Le modèle est garanti
de renvoyer `{"punchlines": [...]}` — pas de parsing défensif à écrire.

`_load_dotenv()` (l.1046) charge `.env` **sans écraser** l'existant
(`os.environ.setdefault`) : une variable exportée dans le shell reste
prioritaire.

---

## `generate_punchlines(...)` — l.1173 : le cache et la dégradation

### La clé de cache (l.1183)

```python
key = hashlib.md5(f"{_llm_backend()}|{model}|{preprompt}|{count}|{seed}".encode()).hexdigest()
```

Cinq composantes. **Le backend en fait partie** : LM Studio et Claude ne
produisent pas les mêmes textes, leurs résultats ne doivent pas se mélanger dans
le même cache.

### La dégradation (l.1191)

```python
try:
    punchlines = _call_llm(preprompt, count, seed, model)[:count]
except Exception:
    return []
```

`except Exception` large, **volontairement**. Serveur éteint, clé absente,
timeout, JSON malformé, quota dépassé — tout retourne `[]`.

C'est le cas d'usage qui commande : un lot de 10 vidéos ne doit pas mourir
parce que LM Studio a été fermé. Les vidéos sortent sans texte, et
`generate_video` le signale dans son log :

```
aucune punchline (LLM indisponible ? rendu sans texte)
```

### Sortie anticipée (l.1179)

```python
if count <= 0 or not preprompt.strip():
    return []
```

Pas de préprompt = pas d'appel. On ne paie pas pour une requête vide.

---

## `apply_subtitles(edl, config, seed, cache_dir)` — l.1201

Le point d'entrée du bloc.

```python
sub = config.get("subtitles") or {}
if not sub.get("enabled"):
    return edl
if sub.get("mode") == "fixe":
    text = sub.get("text", "")
    for entry in edl:
        entry["caption"] = text
    return edl
n = assign_caption_slots(edl, float(sub.get("min_dur", 1.4)))
lines = generate_punchlines(sub.get("preprompt", ""), n, seed, cache_dir,
                            sub.get("model", "claude-opus-4-8"))
for entry in edl:
    i = entry.get("caption_slot", 0)
    entry["caption"] = lines[i] if i < len(lines) else ""
```

Trois chemins :

1. **Désactivé** → EDL inchangé, aucune clé `caption`.
2. **Mode fixe** → même texte partout. Ni créneaux, ni LLM, ni cache.
3. **Mode llm** → créneaux, génération, distribution.

### `lines[i] if i < len(lines) else ""`

Si le LLM en a rendu moins que demandé (ou `[]`), les créneaux restants
reçoivent une chaîne vide. `render` teste `if cap and font` et n'ajoute pas de
`drawtext`. Aucune position ne peut planter.

---

## Le contrat avec `generate_niche.py`

```python
captions = sorted({e["caption"] for e in edl if e.get("caption")})
```

`generate_video` collecte les captions **réellement posées** et les retourne
dans `info["captions"]`. `generate_niche.py` les stocke en base.

Le commentaire dans `generate_niche.py` documente un bug corrigé : un repli sur
le texte fixe avait été ajouté, mais il se déclenchait justement quand `enabled`
est `False` — l'UI garde la valeur en state même champ masqué. Résultat : une
caption enregistrée sur une vidéo qui n'en portait aucune.

**La règle** : `info["captions"]` reflète fidèlement la vidéo. Pas de repli à
ajouter à ce niveau.

---

## Les polices

### Deux systèmes superposés

```python
_FONT_FILES = {  # nom logique -> fichier embarqué (licences OFL)
    "impact":    "Anton-Regular.ttf",
    "classique": "Montserrat-ExtraBold.ttf",
    "sobre":     "OpenSans-Bold.ttf",
    "condensee": "BebasNeue-Regular.ttf",
    "douce":     "Baloo2-Bold.ttf",
    "elegante":  "CormorantGaramond-SemiBold.ttf",
}
```

Six polices **embarquées** dans `assets/fonts/`, sous licence OFL — donc
redistribuables, et identiques sur macOS, Windows et Linux.

```python
def resolve_caption_font(name: str) -> str | None:
    path = FONTS_DIR / _FONT_FILES.get(name, _FONT_FILES["impact"])
    if path.is_file():
        return str(path)
    return _caption_font()
```

Deux replis en cascade : nom inconnu → `impact` ; fichier absent → polices
système (`_CAPTION_FONTS`, l.1243, qui liste des chemins macOS puis Linux).

Le nom logique `"impact"` pointe vers Anton, pas vers Impact — Anton en est
l'équivalent libre.

---

## Les deux échappements

### `_drawtext_escape(text)` — l.1011

```python
out = text.replace("\\", "\\\\")
for ch in (":", "'", "%", ",", ";", "[", "]"):
    out = out.replace(ch, "\\" + ch)
out = out.replace("\r\n", "\n").replace("\n", "\\n")
```

Le filtergraph FFmpeg utilise `:` comme séparateur d'options et `,` comme
séparateur de filtres. Une punchline contenant l'un des deux casserait le
parseur.

**L'ordre est critique** : le doublement des antislashs vient en premier, et la
conversion des retours à la ligne en **dernier** — sinon le `\n` qu'on vient
d'introduire serait lui-même doublé.

### `_drawtext_fontfile(path)` — l.1023

```python
return path.replace("\\", "/").replace(":", "\\\\:")
```

Le cas Windows, documenté l.1024-1028 :

> Sous Windows, les antislashs et le deux-points du lecteur (`C:\...`) cassent
> le parseur, qui applique **DEUX** niveaux d'unescape : le `:` doit donc être
> précédé de **deux** antislashs (`C\\:/...`), un seul ne suffit pas.

No-op sur les chemins POSIX. Bug trouvé au déploiement sur la tour Windows.

---

## Ce qui est testé

`tests/test_subtitles.py`

`assign_caption_slots` est **pure** — testée directement sur un EDL fabriqué.

Les appels LLM sont mockés via `monkeypatch` sur `_call_llm`, ce que la
résolution par `globals()` rend possible. On teste : le cache est bien relu, un
échec dégrade en `[]`, le mode fixe ne déclenche aucun appel.

---

## Les réglages

| Clé (`subtitles.*`) | Défaut | Effet |
|---|---|---|
| `enabled` | `False` | interrupteur |
| `mode` | `"llm"` | `"fixe"` = texte manuel, `"llm_unique"` = une punchline générée, stable du début à la fin |
| `text` | `""` | le texte du mode fixe |
| `preprompt` | `""` | consigne de style |
| `min_dur` | `1.4` | durée min. d'un créneau |
| `x`, `y` | `0.5`, `0.74` | ancrage, en fraction d'écran |
| `size` | `64` | taille en px |
| `font` | `"impact"` | une des 6 polices embarquées |
| `model` | `"claude-opus-4-8"` | backend anthropic uniquement |

### Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `LLM_BACKEND` | `lmstudio` | backend primaire |
| `LLM_FALLBACK` | — | backend de repli |
| `LMSTUDIO_BASE_URL` | `http://localhost:1234/v1` | endpoint local |
| `LMSTUDIO_MODEL` | `local-model` | modèle chargé |
| `ANTHROPIC_API_KEY` | — | requis pour le backend anthropic |

`x`, `y` et `size` valent pour **les deux modes**. Le texte est centré sur son
point d'ancrage.
