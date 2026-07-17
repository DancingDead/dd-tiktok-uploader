# Design — Presets de montage : palette d'ambiances + leviers moteur

Date : 2026-07-17
Statut : validé (brainstorming)

## Objectif

Rendre l'usine à vidéos **adaptable à tout type de niche** en enrichissant la
palette d'ambiances de montage. Deux volets :

1. **Quatre nouveaux leviers moteur** (étalonnage couleur, grain/VHS, intensité
   de glitch, slow-mo global) qui donnent aux presets une vraie diversité
   d'ambiance, au-delà des simples on/off actuels.
2. **Cinq modèles** de pré-remplissage côté UI (au lieu de 2), couvrant les
   palettes Doux/Chill, Hype/Phonk, Cinématique et Rétro/VHS.

Périmètre borné (YAGNI) : pas de LUT externes, pas de transitions, pas de
textures avancées. Les leviers réutilisent les mécanismes FFmpeg déjà en place.

## Existant (rappel)

- Un **preset** = un jeu d'overrides nommé stocké en base (`db.py`), fusionné
  via `DEFAULT_CONFIG ← settings.json ← preset` (`db.effective_config` /
  `beatsync.merge_settings`). **Contrainte clé** : `merge_settings` ne conserve
  que les clés présentes dans la base → toute nouvelle clé de config doit être
  ajoutée à `DEFAULT_CONFIG`, sinon elle est silencieusement ignorée.
- Le rendu (`beatsync.render` / le constructeur de filtres de segment autour de
  la ligne 838) assemble une chaîne `post` FFmpeg par segment :
  `fps → zoom → flash → RGB/glitch → drawtext → setsar/format → tpad`.
- Le slow-mo pré-drop (`effects.speed`) pose `entry["speed"]=0.5` dans
  `build_edl` ; `render` consomme `source = duration × speed` et applique
  `setpts=(PTS-STARTPTS)/speed`. **La durée sur la timeline ne change pas** :
  seule la quantité de source consommée varie → les coupes restent calées sur
  les beats et le rendu reste reproductible.
- Le front React (`frontend/src/features/presets/`) : `PresetsTab.tsx` propose
  2 modèles de pré-remplissage (`PRESET_TEMPLATES` : `doux`, `energique`) ;
  `PresetEditor.tsx` expose les champs d'overrides.

## Volet 1 — Leviers moteur

Chaque levier ajoute une clé à `DEFAULT_CONFIG` et, quand c'est un filtre de
rendu, une **fonction pure et testée** qui construit le fragment FFmpeg (jamais
d'I/O). Ordre d'insertion dans la chaîne `post` : après `flash`, avant/autour du
`glitch`, mais **avant** `drawtext` (les punchlines restent nettes) et avant
`format=yuv420p`.

### 1.1 Étalonnage couleur — `color_grade`

- Clé : `"color_grade"` dans `DEFAULT_CONFIG`, défaut `"neutre"`.
- Valeurs autorisées : `neutre` | `chaud` | `froid` | `delave`.
- Fonction pure `color_grade_filter(grade: str) -> str` → fragment `eq=…` (ou
  `""` pour `neutre` / valeur inconnue). Valeurs indicatives (ajustables) :
  - `chaud`  : `eq=gamma_r=1.06:gamma_b=0.94:saturation=1.05`
  - `froid`  : `eq=gamma_b=1.06:gamma_r=0.94:saturation=0.98`
  - `delave` : `eq=saturation=0.72:contrast=0.94:brightness=0.03`
- Appliqué à **chaque** segment (dans `post`, après les effets, avant drawtext).

### 1.2 Grain / VHS — `grain`

- Clé : `"grain"` dans `DEFAULT_CONFIG`, défaut `0.0`, plage `0.0`–`1.0`.
- Fonction pure `grain_filter(amount: float) -> str` :
  - `amount <= 0` → `""`.
  - sinon `noise=alls={round(amount*24)}:allf=t` (bruit temporel proportionnel).
  - `amount >= 0.6` : ajoute une légère dérive chroma permanente pour l'effet
    VHS, ex. `,rgbashift=rh=2:bh=-2`.
- Appliqué à chaque segment. Pas de `geq` (trop lent).

### 1.3 Intensité de glitch — `accents.glitch` (bool → float)

- La clé **reste** `accents.glitch` (compat ascendante). Elle accepte désormais
  un nombre `0.0`–`1.0`. Coercion via `glitch_amount(accents: dict) -> float` :
  - `True` → `0.6`, `False`/absent → `0.0`, nombre → clampé `[0,1]`.
- `build_edl` : la proportion de segments intenses du drop qui reçoivent l'effet
  `glitch` est pilotée par ce montant (le seuil seedé actuel ~0.25 devient
  `amount`). `amount=0` → aucun glitch (comme `False` aujourd'hui).
- `render` : la force du `rgbashift` du glitch peut être modulée par `amount`
  (garder simple ; l'essentiel est la proportion, pilotée dans `build_edl`).
- Déterminisme préservé : le tirage reste basé sur le `random.Random(seed)`
  local de `build_edl`.

### 1.4 Slow-mo global — `clip_speed`

- Clé : `"clip_speed"` dans `DEFAULT_CONFIG`, défaut `1.0`, plage `0.5`–`1.5`.
- `build_edl` : la vitesse de base de chaque segment devient `clip_speed` au lieu
  de `1.0` (ligne ~480). Le **gasp pré-drop garde sa priorité** : quand
  `effects.speed` s'applique en buildup juste avant le drop, il force `0.5`.
- Aucun changement de mécanisme dans `render` (il lit déjà `entry["speed"]` et
  recalcule `source = duration × speed`). Timeline inchangée → beats préservés,
  reproductible.
- Note d'implémentation : vérifier que la logique existante de disponibilité de
  source gère `clip_speed > 1` (segment qui demande plus de source que la durée
  du clip) — comportement de repli existant conservé, pas de régression.

### Compat serveur (webui.py)

- `EDITABLE_SETTINGS` / `DEFAULT_CONFIG` : les 3 nouvelles clés numériques/enum
  circulent via merge → OK une fois ajoutées à `DEFAULT_CONFIG`.
- `NUMERIC_OVERRIDE_KEYS` : ajouter `grain`, `clip_speed`. La coercion glitch se
  fait dans `accents` (imbriqué) — étendre `coerce_overrides` pour coercer
  `accents.glitch` en float si présent.
- `color_grade` : valider contre l'enum autorisé dans `coerce_overrides` →
  `ValueError` (→ 400) si valeur inconnue. Défense cohérente avec l'existant.

## Volet 2 — Cinq modèles (front)

`PRESET_TEMPLATES` dans `PresetsTab.tsx` passe à 5 entrées, chacune un
pré-remplissage d'`Overrides`. Valeurs indicatives (l'utilisateur ajuste ensuite
dans l'éditeur) :

| Modèle | cut_mode | strobe | effets | color_grade | grain | glitch | clip_speed | police |
|---|---|---|---|---|---|---|---|---|
| **Doux** | fixed /4 | 0 | aucun | chaud | 0.1 | 0 | 0.9 | douce |
| **Chill / Lo-fi** | energy | 0 | zoom | delave | 0.2 | 0 | 0.85 | elegante |
| **Énergique / Phonk** | energy | 16 | tous | froid | 0 | 0.35 | 1.0 | impact |
| **Cinématique** | energy | 0 | zoom + slow-mo | froid | 0.1 | 0 | 0.9 | elegante |
| **Rétro / VHS** | fixed /2 | 0 | aucun | delave | 0.8 | 0.7 | 1.0 | sobre |

Les 5 boutons de modèle remplacent les 2 boutons actuels (Doux/Énergique).

## Volet 3 — UI React (`PresetEditor.tsx`, `api.ts`)

Nouveaux contrôles dans l'éditeur :

- **Ambiance couleur** : `Select` (neutre / chaud / froid / délavé).
- **Grain** : `NumberField` 0–1, step 0.05.
- **Vitesse clip** : `NumberField` 0.5–1.5, step 0.05.
- **Intensité glitch** : le `Toggle` « Micro-glitch » devient un `NumberField`
  0–1 (step 0.05). L'ancien bool est lu en fallback (0.6 si `true`).

`api.ts` : type `Overrides` étendu (`color_grade?`, `grain?`, `clip_speed?`,
`accents.glitch` accepte `number`).

**Décision** : l'UI vanilla (`templates/index.html`, fallback dev) **n'est pas
mise à jour** — le React est l'UI principale. Le backend reste cohérent quelle
que soit l'UI (une valeur absente retombe sur le défaut de `DEFAULT_CONFIG`).

## Volet 4 — Tests

- **Helpers purs** (`tests/test_build_edl.py` ou nouveau fichier) :
  - `color_grade_filter` : chaque valeur → fragment attendu ; `neutre`/inconnu → `""`.
  - `grain_filter` : `0` → `""` ; croissant ; seuil VHS ≥0.6 ajoute la dérive chroma.
  - `glitch_amount` : `True`→0.6, `False`/absent→0, nombre clampé.
- **build_edl** :
  - `clip_speed` propagé à `entry["speed"]` de chaque segment (hors gasp).
  - le gasp pré-drop force toujours `0.5` malgré `clip_speed`.
  - proportion de glitch déterministe et croissante avec `amount` à seed fixe.
- **Reproductibilité** : même seed + même config → EDL identique (motif existant).
- **webui** (`tests/test_webui_platform.py`) : `coerce_overrides` accepte
  `grain`/`clip_speed`/`accents.glitch` numériques ; rejette un `color_grade`
  inconnu (→ 400 / ValueError).

## Invariants préservés

1. `load_clips` trie par nom (déterminisme) — inchangé.
2. `build_edl` utilise le `random.Random(seed)` local — les nouveaux tirages
   (glitch) passent par ce RNG.
3. Flags `bitexact` du rendu — inchangés.
4. Quantification des cuts sur la grille de frames — inchangée (les leviers
   n'altèrent pas les timestamps de coupe ; `clip_speed` ne touche que la source
   consommée, pas la durée timeline).
