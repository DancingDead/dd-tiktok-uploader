# Spec — Option « Moment de la track » (fort / calme) dans les presets

**Date** : 2026-07-20
**Statut** : validé, prêt pour plan d'implémentation

## Problème / objectif

Aujourd'hui, un montage cadre **toujours** sur le moment fort du morceau : `find_drop`
repère l'instant de contraste d'énergie maximal, `resolve_window` cadre la fenêtre
autour (`buildup` avant + reste après), et `build_edl` applique le traitement énergique
(strobo, gasp slow-mo, coupes rapides) parce qu'un `drop_time` est présent.

On veut pouvoir produire des vidéos **plus douces / planantes** en cadrant plutôt sur
une **section calme** du morceau. Le choix se fait **dans le preset**.

## Décisions de cadrage (issues du brainstorming)

- L'option pilote **uniquement quel passage** du morceau est utilisé. Le *look* (couleur,
  grain, vitesse, glitch) reste géré par les presets d'ambiance existants, orthogonaux.
- La section « calme » = **la fenêtre la plus calme du morceau, où qu'elle soit** (intro,
  pont, breakdown), en **évitant un silence/fade** de début.
- Pas de nouveaux réglages d'adoucissement : en mode calme il n'y a pas de `drop_time`,
  donc `build_edl` prend déjà son chemin « sans drop » (pas de strobo/gasp, coupes plus
  espacées car l'énergie est basse). **`build_edl` n'est pas modifié.**

## Design (approche retenue : sélection au niveau moteur)

### 1. Champ de config

Ajouter à `DEFAULT_CONFIG` (`beatsync.py:21`) :

```python
"section": "drop",   # "drop" = moment fort (build-up + drop, défaut) | "calm" = passage calme
```

- Défaut `"drop"` → **100 % rétrocompatible** : presets et bases existants inchangés.
- S'empile comme les autres champs via `db.effective_config` (`DEFAULT_CONFIG ← settings.json ← preset`).
- Valeurs acceptées : `"drop"` | `"calm"`. Toute autre valeur → traitée comme `"drop"`
  (dégradation sûre ; à normaliser côté serveur avec les autres coercions de champs).

### 2. Sélection du passage calme (moteur, pur)

Nouvelle fonction **pure** `find_calm(analysis, config, duration)` dans `beatsync.py`,
miroir de `find_drop` :

- Construit la **même grille d'énergie lissée** que `find_drop` (`dt = 0.25`, interpolation
  sur `energy_times`/`energy`, lissage par convolution ~2 s).
- Fenêtre glissante de longueur `W = round(duration / dt)` beats de grille ; pour chaque
  début candidat `i`, énergie moyenne de la fenêtre via somme cumulée :
  `mean[i] = (csum[i+W] - csum[i]) / W`.
- **Garde anti-silence** : soit `amplitude = energy.max() - energy.min()` et
  `floor = energy.min() + 0.15 * amplitude`. On restreint le choix aux fenêtres dont la
  moyenne est `>= floor` (exclut les intros/fades quasi muets). Si **aucune** fenêtre ne
  passe le plancher (morceau globalement très faible), on retombe sur l'ensemble des
  fenêtres (pas de plancher) pour garantir un résultat.
- Choix = fenêtre à **moyenne minimale** parmi les candidates (`argmin` → première en cas
  d'égalité : déterministe).
- Retourne le **début** de la fenêtre calé sur le beat le plus proche (comme `find_drop`).
- Retourne `None` si le morceau est **trop court** pour une fenêtre pleine
  (`len(grid) < W`) → fallback géré par `resolve_window`.

`find_calm` est **déterministe** (aucun RNG), donc n'affecte pas la reproductibilité.

### 3. Cadrage — `resolve_window`

Brancher sur `config["section"]` (uniquement quand `start` n'est pas forcé par l'appelant
et `duration != "full"`) :

- `"drop"` (défaut) → comportement **inchangé** : `find_drop` + `start = drop - buildup`.
- `"calm"` →
  - `calm_start = find_calm(analysis, config, duration)` ;
  - `config["drop_time"] = None` (pas de drop → `build_edl` chemin doux) ;
  - `start = calm_start` si non `None`, sinon `0.0` (fallback) ;
  - `end = start + duration`, puis `snap_end_to_phrase(end, drop_time=None, …)` retourne
    `end` inchangé (pas de drop) — cohérent.

Cas limites :
- `start` explicitement fourni (CLI `--start`) → prioritaire, la section est ignorée
  (comportement actuel conservé).
- `duration == "full"` → cadrer une « section » n'a pas de sens (tout le morceau) ; en
  mode `"calm"` on met simplement `drop_time = None` et `start = 0.0`.

### 4. Interface (éditeur de preset)

Dans `frontend/src/features/presets/PresetsTab.tsx` (éditeur de preset), ajouter un
sélecteur **« Moment de la track »** à deux choix :

- **Fort (build-up + drop)** → `section: "drop"` (défaut)
- **Calme (passage planant)** → `section: "calm"`

Mappé sur le champ `section` du preset, dans le même style que les contrôles existants.
Les modèles d'ambiance ne touchent pas `section` (orthogonal) — un preset calme se
combine librement avec n'importe quelle ambiance.

### 5. CLI (cohérence)

Ajouter un argument `--section {drop,calm}` (défaut `drop`) à `main()` pour tester le mode
en ligne de commande, aligné sur le champ de config.

## Tests

Fonctions pures → testables sans I/O ni rendu (comme `find_drop`) :

- `find_calm` : morceau synthétique avec un **pont calme** au milieu → retourne un start
  dans ce pont ; morceau à énergie **plate** → retourne un start valide (pas de crash) ;
  morceau avec **intro silencieuse** puis section calme plus loin → **évite l'intro**
  (garde anti-silence) ; morceau **plus court** que la fenêtre → `None`.
- `resolve_window` en `section="calm"` → `config["drop_time"] is None` et `start` dans la
  zone calme ; en `section="drop"` → comportement inchangé (test de non-régression).
- Déterminisme : deux appels `find_calm` sur la même analyse → même résultat.

## Hors périmètre (YAGNI)

- Pas de réglages d'adoucissement dédiés (coupes, effets) au mode calme.
- Pas de bascule automatique d'ambiance.
- Pas de choix « intro » vs « pont » : on prend la plus calme, point.

## Invariants préservés

- **Reproductibilité** : défaut `"drop"` inchangé ; `find_calm` déterministe.
- **`build_edl` intact** : le mode calme repose entièrement sur `drop_time = None`.
- **Rétrocompatibilité** : bases et presets existants continuent en mode `"drop"`.
