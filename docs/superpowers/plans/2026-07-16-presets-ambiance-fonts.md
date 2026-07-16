# Presets d'ambiance + polices de sous-titres — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Six polices de punchlines embarquées sélectionnables par preset, et deux modèles de preset (Doux / Énergique) pré-remplissables à la création.

**Architecture:** `assets/fonts/` (TTF, licences OFL) + résolution nom logique → fichier dans `beatsync.py` (repli sur les polices système actuelles) ; tests de garde sur `strobe_beats: 0` et `effects.speed: false` ; côté front, un Select « Police » dans l'éditeur de preset et deux boutons de pré-remplissage.

**Tech Stack:** Python (beatsync), pytest, React/TypeScript (frontend), pas de test runner front (vérification : `npm run build` + `npm run lint`).

## Global Constraints

- Spec : `docs/superpowers/specs/2026-07-16-presets-ambiance-fonts-design.md`
- Commandes : `uv run pytest` (jamais pip) ; front : `npm run build` / `npm run lint` dans `frontend/`
- Reproductibilité : ne rien changer au comportement à config par défaut (seed à seed)
- Noms logiques des polices : `impact` (défaut), `classique`, `sobre`, `condensee`, `douce`, `elegante`
- Jamais de publication automatique (hors périmètre)

---

### Task 1: Polices embarquées + résolution dans beatsync

**Files:**
- Create: `assets/fonts/Anton-Regular.ttf`, `assets/fonts/Montserrat-ExtraBold.ttf`, `assets/fonts/OpenSans-Bold.ttf`, `assets/fonts/BebasNeue-Regular.ttf`, `assets/fonts/Baloo2-Bold.ttf`, `assets/fonts/CormorantGaramond-SemiBold.ttf`
- Modify: `beatsync.py` (`DEFAULT_CONFIG["subtitles"]` ~l.38, bloc `_CAPTION_FONTS` ~l.585, `_segment_filters` ~l.853)
- Test: `tests/test_subtitles.py`

**Interfaces:**
- Consumes: `_caption_font()` existante (repli système), `_segment_filters(entry, config)` (`beatsync.py:817`)
- Produces: `FONTS_DIR: Path`, `_FONT_FILES: dict[str, str]`, `resolve_caption_font(name: str) -> str | None` ; clé `DEFAULT_CONFIG["subtitles"]["font"] = "impact"`

- [ ] **Step 1: Télécharger les six polices**

Via google-webfonts-helper (sert des TTF statiques par graisse) :

```bash
cd /Users/theoherve/PycharmProjects/dd-tiktok-uploader
mkdir -p assets/fonts /tmp_fonts 2>/dev/null || true
DL=/private/tmp/claude-501/-Users-theoherve-PycharmProjects-dd-tiktok-uploader/f7c689c5-14bc-43c0-a42e-001760eb0ed5/scratchpad
for spec in "anton:400:Anton-Regular" "montserrat:800:Montserrat-ExtraBold" \
            "open-sans:700:OpenSans-Bold" "bebas-neue:400:BebasNeue-Regular" \
            "baloo-2:700:Baloo2-Bold" "cormorant-garamond:600:CormorantGaramond-SemiBold"; do
  IFS=: read fam wght out <<< "$spec"
  curl -sL "https://gwfh.mranftl.com/api/fonts/${fam}?download=zip&subsets=latin&variants=${wght}&formats=ttf" \
       -o "$DL/${fam}.zip"
  unzip -o -j "$DL/${fam}.zip" "*.ttf" -d "$DL/${fam}/"
  mv "$DL/${fam}/"*.ttf "assets/fonts/${out}.ttf"
done
file assets/fonts/*.ttf   # attendu : 6 lignes "TrueType Font data"
```

Si le service est indisponible : télécharger chaque famille à la main sur
https://fonts.google.com (bouton « Download family »), extraire le TTF de la
graisse voulue, renommer selon les noms ci-dessus.

Vérifier la taille totale (`du -sh assets/fonts` ; attendu ≈ 1 Mo, tolérer
jusqu'à ~3 Mo). Aucun commit encore (avec le code à l'étape 5).

- [ ] **Step 2: Écrire les tests qui échouent**

Ajouter à `tests/test_subtitles.py` (les imports du fichier exposent déjà
`beatsync` et `DEFAULT` ; ajouter `resolve_caption_font` à l'import `from
beatsync import (...)`) :

```python
# --- Polices embarquées ------------------------------------------------------


def test_resolve_caption_font_known_names():
    for name, filename in beatsync._FONT_FILES.items():
        path = resolve_caption_font(name)
        assert path is not None and path.endswith(filename)


def test_resolve_caption_font_unknown_name_falls_back_to_impact():
    assert resolve_caption_font("gothique") == resolve_caption_font("impact")


def test_resolve_caption_font_missing_file_falls_back_to_system(monkeypatch, tmp_path):
    monkeypatch.setattr(beatsync, "FONTS_DIR", tmp_path)  # dossier vide
    assert resolve_caption_font("douce") == beatsync._caption_font()


def test_default_config_has_font_key():
    assert DEFAULT["subtitles"]["font"] == "impact"


def test_merge_settings_accepts_subtitles_font():
    from beatsync import merge_settings
    merged = merge_settings(dict(DEFAULT), {"subtitles": {"font": "douce"}})
    assert merged["subtitles"]["font"] == "douce"
    assert merged["subtitles"]["enabled"] is False  # le reste est préservé


def test_segment_filters_uses_configured_font():
    entry = {"timeline_start": 0, "duration": 1.0, "effects": [], "layout": "crop",
             "focus_x": 0.5, "speed": 1.0, "caption": "CHILL"}
    config = dict(DEFAULT, subtitles={**DEFAULT["subtitles"], "font": "douce"})
    vf = " ".join(_segment_filters(entry, config))
    assert "Baloo2-Bold.ttf" in vf
```

(`DEFAULT` est l'alias du fichier pour `DEFAULT_CONFIG` ; `_segment_filters`
est déjà importé pour les tests drawtext existants — sinon compléter l'import.)

- [ ] **Step 3: Vérifier l'échec**

Run: `uv run pytest tests/test_subtitles.py -k "font" -v`
Expected: ImportError sur `resolve_caption_font` (collection) — puis, une fois
le nom importable, échecs pour absence de `_FONT_FILES`/clé `font`. Chaque test
doit échouer parce que la fonctionnalité manque, pas sur une typo.

- [ ] **Step 4: Implémenter**

Dans `beatsync.py` :

a) `DEFAULT_CONFIG["subtitles"]` (l.38-43) gagne la clé :

```python
    "subtitles": {                      # punchlines incrustées, générées par le LLM
        "enabled": False,               # désactivé par défaut
        "preprompt": "",                # consigne de style (ex. « punchlines motivation gym »)
        "min_dur": 1.4,                 # durée min. d'affichage d'une punchline (lisibilité)
        "model": "claude-opus-4-8",     # modèle de génération
        "font": "impact",               # police embarquée : impact|classique|sobre|condensee|douce|elegante
    },
```

b) Après le bloc `_CAPTION_FONTS` (l.585-598), ajouter :

```python
FONTS_DIR = Path(__file__).parent / "assets" / "fonts"

_FONT_FILES = {  # nom logique -> fichier embarqué (licences OFL)
    "impact": "Anton-Regular.ttf",
    "classique": "Montserrat-ExtraBold.ttf",
    "sobre": "OpenSans-Bold.ttf",
    "condensee": "BebasNeue-Regular.ttf",
    "douce": "Baloo2-Bold.ttf",
    "elegante": "CormorantGaramond-SemiBold.ttf",
}


def resolve_caption_font(name: str) -> str | None:
    """Chemin de la police d'un nom logique ; nom inconnu = impact ;
    fichier absent = repli sur les polices système (_caption_font)."""
    path = FONTS_DIR / _FONT_FILES.get(name, _FONT_FILES["impact"])
    if path.is_file():
        return str(path)
    return _caption_font()
```

c) Dans `_segment_filters` (l.853), remplacer :

```python
    font = _caption_font()
```

par :

```python
    font = resolve_caption_font(config.get("subtitles", {}).get("font", "impact"))
```

- [ ] **Step 5: Vérifier le passage + suite complète**

Run: `uv run pytest tests/test_subtitles.py -v` puis `uv run pytest`
Expected: tous PASS (les tests drawtext existants passent encore : Anton est
présent, le fontfile change de chemin mais `drawtext` reste dans le filtre).

- [ ] **Step 6: Commit**

```bash
git add assets/fonts beatsync.py tests/test_subtitles.py
git commit -m "feat(subtitles): six polices embarquées sélectionnables par preset"
```

---

### Task 2: Tests de garde moteur — strobe_beats 0 et speed false

**Files:**
- Test: `tests/test_build_edl.py` (fixtures existantes : `make_analysis()`, constantes `BPM/BEAT/DURATION`)
- Modify: `beatsync.py` (`build_edl`) **seulement si** un test révèle un défaut

**Interfaces:**
- Consumes: `build_edl(analysis, clips, config, seed)`, `make_analysis()` et
  `make_clips()` (`tests/test_build_edl.py:23` et `:52`)
- Produces: rien de nouveau — garanties verrouillées par tests

- [ ] **Step 1: Écrire les tests**

Ajouter à `tests/test_build_edl.py`, en réutilisant `make_analysis()` et
`make_clips()` :

```python
def test_strobe_beats_zero_disables_strobe():
    """Preset doux : strobe_beats=0 -> aucune section à 1 beat forcé au drop."""
    analysis = make_analysis()
    config = dict(DEFAULT_CONFIG, drop_time=30.0, start=20.0, end=50.0,
                  strobe_beats=0, cut_mode="fixed", cut_every=4)
    edl = build_edl(analysis, make_clips(), config, seed=7)
    # En mode fixe à 4 beats sans strobo, aucun segment ne doit durer ~1 beat
    # (le seul cas légitime serait le résidu de fin de fenêtre : tolérer le dernier).
    for entry in edl[:-1]:
        assert entry["duration"] > BEAT * 1.5


def test_speed_false_disables_gasp():
    """Preset doux : effects.speed=False -> aucun slow-mo avant le drop."""
    analysis = make_analysis()
    config = dict(DEFAULT_CONFIG, drop_time=30.0, start=20.0, end=50.0,
                  effects={**DEFAULT_CONFIG["effects"], "speed": False})
    edl = build_edl(analysis, make_clips(), config, seed=7)
    assert all(entry["speed"] == 1.0 for entry in edl)


def test_default_config_edl_unchanged_by_new_font_key():
    """Non-régression : la clé subtitles.font n'affecte pas l'EDL."""
    analysis = make_analysis()
    config = dict(DEFAULT_CONFIG, start=0.0, end=30.0)
    a = build_edl(analysis, make_clips(), config, seed=42)
    b = build_edl(analysis, make_clips(), config, seed=42)
    assert a == b
```

- [ ] **Step 2: Lancer et interpréter**

Run: `uv run pytest tests/test_build_edl.py -k "strobe_beats_zero or speed_false or unchanged_by_new_font" -v`

Deux issues possibles :
- **PASS immédiat** : attendu — ce sont des tests de garde qui verrouillent un
  comportement dont dépendent les presets Doux (la lecture du code suggère que
  `drop_idx <= i < drop_idx + 0` est une plage vide et que le gasp est gardé
  par `effects_cfg.get("speed")`). Les garder tels quels.
- **FAIL** : le moteur a un cas limite → le corriger dans `build_edl`
  (minimalement), relancer jusqu'au vert.

- [ ] **Step 3: Suite complète**

Run: `uv run pytest`
Expected: PASS, aucune régression.

- [ ] **Step 4: Commit**

```bash
git add tests/test_build_edl.py
git commit -m "test(edl): verrouille strobe_beats=0 et speed=false pour le preset doux"
```

(ajouter `beatsync.py` au commit si une correction moteur a été nécessaire,
avec un message `fix(edl): ...` à la place)

---

### Task 3: Front — Select « Police » + modèles Doux / Énergique

**Files:**
- Modify: `frontend/src/lib/api.ts:36-46` (type `Overrides`)
- Modify: `frontend/src/features/presets/PresetEditor.tsx`
- Modify: `frontend/src/features/presets/PresetsTab.tsx`

**Interfaces:**
- Consumes: `Overrides`, `PresetEditor({preset, onSaved, onDeleted, refresh})`,
  `resolve_caption_font` côté serveur via la config fusionnée (aucune API nouvelle)
- Produces: `Overrides.subtitles?: { font?: string }` ; prop optionnelle
  `template?: Overrides` sur `PresetEditor` ; constante `PRESET_TEMPLATES`

- [ ] **Step 1: Étendre le type Overrides**

Dans `frontend/src/lib/api.ts`, ajouter au type `Overrides` :

```typescript
export type Overrides = {
  effects?: { zoom?: boolean; flash?: boolean; shake?: boolean; speed?: boolean }
  accents?: { rgb?: boolean; glitch?: boolean }
  delogo?: boolean
  chrono?: boolean
  min_presence?: number
  cut_mode?: string
  cut_every?: number
  buildup?: number
  strobe_beats?: number
  subtitles?: { font?: string }
}
```

(Vérifier que le type `Settings` en dessous — `Required<Pick<Overrides, ...>>` —
ne liste pas `subtitles` ; ne pas l'y ajouter.)

- [ ] **Step 2: Éditeur — état, Select police, overrides**

Dans `PresetEditor.tsx` :

a) Constante des polices (haut du fichier, après les imports) :

```typescript
// Polices embarquées (assets/fonts/) — noms logiques côté moteur.
export const CAPTION_FONTS = [
  { value: "impact", label: "Impact (edit)" },
  { value: "classique", label: "Classique (TikTok)" },
  { value: "sobre", label: "Sobre" },
  { value: "condensee", label: "Condensée (sport)" },
  { value: "douce", label: "Douce (arrondie)" },
  { value: "elegante", label: "Élégante (fine)" },
] as const
```

b) Props : accepter un modèle de pré-remplissage :

```typescript
type Props = {
  preset: Preset | null
  template?: Overrides // pré-remplissage à la création (modèles Doux/Énergique)
  onSaved: (id: number) => void
  onDeleted: () => void
  refresh: () => Promise<void>
}
```

c) Dans le composant : `const o = preset?.overrides ?? template ?? {}` (remplace
la ligne 79) et nouvel état :

```typescript
  const [font, setFont] = useState(o.subtitles?.font ?? "impact")
```

d) Dans `save()`, ajouter aux overrides : `subtitles: { font },`

e) Nouvelle carte après « Cadrage & contenu » (mêmes composants Select que la
carte Rythme) :

```tsx
      <Card>
        <CardHeader>
          <CardTitle>Punchlines</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-1.5">
            <Label>Police</Label>
            <Select value={font} onValueChange={setFont}>
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CAPTION_FONTS.map((f) => (
                  <SelectItem key={f.value} value={f.value}>
                    {f.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>
```

- [ ] **Step 3: Onglet — boutons modèles**

Dans `PresetsTab.tsx` :

a) Constante des modèles (haut du fichier) :

```typescript
import type { AppState, Overrides } from "@/lib/api"

// Modèles d'ambiance : pré-remplissent l'éditeur à la création.
const PRESET_TEMPLATES: Record<"doux" | "energique", Overrides> = {
  energique: {
    cut_mode: "energy",
    strobe_beats: 16,
    effects: { zoom: true, flash: true, shake: true, speed: true },
    accents: { rgb: true, glitch: true },
    subtitles: { font: "impact" },
  },
  doux: {
    cut_mode: "fixed",
    cut_every: 4,
    strobe_beats: 0,
    effects: { zoom: false, flash: false, shake: false, speed: false },
    accents: { rgb: false, glitch: false },
    subtitles: { font: "douce" },
  },
}
```

b) État : `const [template, setTemplate] = useState<Overrides | undefined>()`.
Le bouton « Nouveau » existant fait `setSelectedId(null); setTemplate(undefined)`.
Sous lui, deux boutons :

```tsx
          <p className="mt-2 text-xs text-muted-foreground">Partir d'un modèle :</p>
          <Button
            variant="outline"
            className="w-full"
            onClick={() => {
              setSelectedId(null)
              setTemplate(PRESET_TEMPLATES.doux)
            }}
          >
            Doux
          </Button>
          <Button
            variant="outline"
            className="w-full"
            onClick={() => {
              setSelectedId(null)
              setTemplate(PRESET_TEMPLATES.energique)
            }}
          >
            Énergique
          </Button>
```

c) Passer le modèle à l'éditeur et forcer le remontage quand il change :

```tsx
        <PresetEditor
          key={selectedId ?? (template ? JSON.stringify(template) : "new")}
          preset={selected}
          template={template}
          onSaved={(id) => {
            setTemplate(undefined)
            setSelectedId(id)
          }}
          onDeleted={() => setSelectedId(null)}
          refresh={refresh}
        />
```

- [ ] **Step 4: Vérifier build + lint + serveur**

```bash
cd frontend && npm run lint && npm run build && cd ..
uv run pytest tests/test_webui_platform.py -v
```

Expected: lint et build sans erreur ; les tests serveur passent (la création
de preset avec `{"subtitles": {"font": "douce"}}` traverse `coerce_overrides`
sans coercition — clé non numérique — et `merge_settings` la fusionne, couvert
par Task 1).

- [ ] **Step 5: Vérification manuelle rapide**

Lancer `uv run python webui.py`, ouvrir l'onglet Presets : créer un preset
depuis le modèle « Doux », vérifier que les champs sont pré-remplis (mode fixe,
4 beats, strobo 0, tout décoché, police Douce), enregistrer, rééditer :
la police doit être conservée.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/features/presets/
git commit -m "feat(presets): modèles Doux/Énergique + choix de la police des punchlines"
```
