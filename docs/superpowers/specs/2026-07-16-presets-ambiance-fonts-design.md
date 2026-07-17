# Presets d'ambiance + polices de sous-titres (lot A)

Date : 2026-07-16
Statut : validé (brainstorming avec Théo)

## Objectif

Permettre de produire des **types de vidéos différents** — contenu doux vs
énergique — sans connaître les clés de config, et de choisir la **police des
punchlines** parmi un set embarqué. Concentré sur la création de contenu ;
**jamais de publication automatique** (périmètre inchangé, décision 2026-07-08).

Découpage validé : ce lot est **config + polices seulement**. Les « vraies »
transitions douces (fondus enchaînés entre plans, zooms lents continus), qui
touchent le moteur de rendu, forment un **lot 2 ultérieur**, hors de cette spec.

## Décisions prises

| Question | Décision |
|---|---|
| Style doux | En deux temps : lot A = config (coupes lentes, zéro effet) ; lot 2 = fondus/zooms lents |
| Polices | Set **embarqué dans le repo** (`assets/fonts/`, licences OFL) — pas de polices système (rendu identique Mac/tour Windows, reproductible), pas d'upload custom (plus tard si besoin) |
| Presets d'ambiance | Boutons « Partir de : Doux / Énergique » qui **pré-remplissent** les overrides à la création d'un preset — presets normaux ensuite, pas de magie en base |

## 1. Polices embarquées

Nouveau dossier `assets/fonts/` (~1 Mo), six polices Google Fonts (OFL) :

| Nom logique | Fichier | Style |
|---|---|---|
| `impact` *(défaut)* | Anton | Impact/edit, équivalent du rendu actuel |
| `classique` | Montserrat ExtraBold | La police « sous-titres TikTok » par excellence |
| `sobre` | Open Sans Bold | Neutre, très lisible |
| `condensee` | Bebas Neue | Majuscules condensées, edits sport/motivation |
| `douce` | Baloo 2 Bold | Arrondie, chaleureuse (preset Doux) |
| `elegante` | Cormorant Garamond SemiBold | Fine, esthétique/mélancolique |

- Nouvelle clé `subtitles.font` dans `DEFAULT_CONFIG` (`beatsync.py`), valeur
  par défaut `"impact"`. Nécessaire pour que `merge_settings` accepte la clé
  dans les presets (les clés inconnues d'un dict imbriqué sont filtrées).
- `_caption_font()` devient une résolution : nom logique → fichier de
  `assets/fonts/` ; **repli** sur la recherche système actuelle (Impact, etc.)
  si le fichier manque ; nom inconnu = comme `"impact"`. Fonction pure de
  résolution testée à part du lookup disque.
- Rendu par défaut : avec Anton embarqué pour `impact`, le rendu ne dépend
  plus de la présence d'Impact sur la machine — plus reproductible
  qu'aujourd'hui. Léger changement visuel possible (Anton ≈ Impact, pas
  identique) : accepté.
- UI : liste déroulante « Police des punchlines » dans l'éditeur de preset
  (champ de `subtitles`), avec les six noms logiques.

## 2. Presets d'ambiance prêts à l'emploi

À la création d'un preset (onglet Presets du front React), deux boutons de
pré-remplissage :

**Énergique** (la config par défaut, posée explicitement) :

```json
{
  "cut_mode": "energy",
  "energy_intervals": [4, 2, 1],
  "strobe_beats": 16,
  "effects": {"zoom": true, "flash": true, "shake": true, "speed": true},
  "accents": {"rgb": true, "glitch": true},
  "subtitles": {"font": "impact"}
}
```

**Doux** :

```json
{
  "cut_mode": "fixed",
  "cut_every": 4,
  "strobe_beats": 0,
  "effects": {"zoom": false, "flash": false, "shake": false, "speed": false},
  "accents": {"rgb": false, "glitch": false},
  "subtitles": {"font": "douce"}
}
```

Les modèles vivent côté front (constante TypeScript) : un preset créé depuis
un modèle est un preset ordinaire (overrides JSON), modifiable avant et après
enregistrement.

Note (2026-07-16) : le modèle Doux utilise `cut_mode: "fixed"` + `cut_every: 4`
plutôt que `energy_intervals` — l'éditeur de presets reconstruit les overrides
à l'enregistrement à partir de ses champs, et perdrait silencieusement une clé
qu'il n'affiche pas. Rythme calme et régulier, même effet recherché, zéro champ
UI en plus.

## 3. Cas limites moteur à garantir (tests dans ce lot)

À vérifier et corriger si besoin dans `build_edl` (`beatsync.py`) :

- `strobe_beats: 0` → **aucune** section strobo après le drop ;
- `effects.speed: false` → **aucun** gasp slow-mo avant l'impact ;
- comportement inchangé à config par défaut (non-régression seed à seed).

## 4. Tests

- Résolution de police : chaque nom logique → chemin attendu ; nom inconnu →
  `impact` ; fichier absent → repli système (pure, testée).
- `merge_settings` : un preset `{"subtitles": {"font": "douce"}}` survit à la
  fusion.
- `build_edl` : `strobe_beats: 0` sans strobo ; `effects.speed: false` sans
  gasp ; même seed + config défaut = même EDL qu'avant (non-régression).
- Rendu : la commande drawtext construite référence le bon `fontfile`
  (test sur la construction du filtre, sans encoder).

## Hors périmètre

- Fondus enchaînés / zooms lents continus (lot 2).
- Upload de polices custom (plus tard si une police signature DD émerge).
- Publication automatique (jamais).
