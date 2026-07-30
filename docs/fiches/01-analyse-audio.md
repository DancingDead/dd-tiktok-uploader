# 01 — Analyse audio

> `analyze_audio` · `find_drop` · `find_calm` — lignes 198-655
> ← [vue d'ensemble](00-beatsync-vue-ensemble.md) · → [02 scan des clips](02-scan-clips.md)

## Ce que fait ce bloc

Transformer un fichier audio en **trois informations** dont tout le reste
dépend : où sont les beats, où est le drop, et quelle est l'énergie à chaque
instant.

---

## `analyze_audio(track_path)` — l.198

```python
return {
    "duration":     float,       # durée du morceau, en s
    "bpm":          float,       # tempo estimé
    "beats":        np.ndarray,  # timestamps des beats, en s
    "energy":       np.ndarray,  # enveloppe RMS
    "energy_times": np.ndarray,  # timestamps de l'enveloppe
}
```

Trois appels librosa, rien de plus : `beat_track`, `rms`, `get_duration`.

### Pourquoi l'import est *dans* la fonction

```python
def analyze_audio(track_path: Path) -> dict:
    import librosa  # import paresseux : coûteux (~2 s), inutile pour la logique pure
```

librosa met ~2 secondes à s'importer. Les tests de `build_edl`, `ramp_speed`,
`usable_intervals` n'en ont aucun besoin — ils travaillent sur des dicts
fabriqués à la main. Un import en tête de fichier ajouterait 2 s à **chaque**
lancement de pytest, pour rien.

Même raison pour `cv2` dans `_char_presence` ([02](02-scan-clips.md)).

### `units="time"`

`beat_track` peut retourner des indices de frames ou des secondes. On demande
des secondes tout de suite : tout le reste du fichier raisonne en secondes, et
convertir plus tard serait une source d'erreurs.

---

## `find_drop(analysis, config)` — l.521

**Retourne** : le timestamp du drop calé sur un beat, ou `None`.

### La définition retenue

> Le drop est l'instant qui **maximise le contraste d'énergie** entre les 8 s
> qui suivent et les 8 s qui précèdent.

C'est une définition volontairement simple. Pas de détection de kick, pas de
modèle. Ça marche parce qu'un drop, musicalement, *est* une rupture d'énergie.

### Les 5 étapes

```
1. Ré-échantillonner l'énergie sur une grille régulière de 0,25 s
   → np.interp, l.529

2. Lisser sur ~2 s (convolution par un noyau plat)
   → sinon chaque coup de caisse claire ferait un pic, l.534

3. Contraste en chaque point = moyenne(8 s après) − moyenne(8 s avant)
   → calculé par somme cumulée : O(n) au lieu de O(n × fenêtre), l.540-542

4. Garde-fou : si contraste_max < 20 % de l'amplitude → None
   → l.545

5. Caler le maximum sur le beat le plus proche
   → l.549-550
```

### Pourquoi la somme cumulée (l.540)

```python
csum = np.concatenate([[0.0], np.cumsum(energy)])
contrast = (csum[idx + window] - csum[idx]) / window - (csum[idx] - csum[idx - window]) / window
```

La moyenne d'une tranche `[a, b]` devient une soustraction : `csum[b] - csum[a]`.
On calcule le contraste de **tous** les instants d'un coup, en vectoriel. La
version naïve (une boucle avec deux `.mean()` par point) serait ~100× plus lente
sur un morceau de 4 minutes.

### Pourquoi le garde-fou (l.545)

```python
if amplitude <= 0.0 or float(contrast.max()) < 0.2 * amplitude:
    return None
```

Sur un morceau à énergie constante — une nappe ambient, une boucle sans
structure — le maximum de contraste existe quand même, mais c'est du bruit. Le
« drop » tomberait au hasard.

Retourner `None` est un **choix de dégradation** : `resolve_window` cadre alors
depuis le début du morceau, et `build_edl` monte sans strobo ni section drop.
La vidéo est moins spectaculaire, mais elle sort. Voir « ne jamais bloquer
l'usine » dans la [vue d'ensemble](00-beatsync-vue-ensemble.md).

### Pourquoi caler sur un beat (l.550)

```python
return float(beats[int(np.argmin(np.abs(beats - drop_time)))])
```

Le maximum de contraste tombe sur la grille de 0,25 s, pas sur un beat. Or tout
`build_edl` raisonne en **index de beat** : le strobo, les impacts, la coupe
garantie. Un drop entre deux beats n'aurait pas d'index. On le recale donc dès
la sortie de `find_drop`, une bonne fois.

---

## `find_calm(analysis, config, duration)` — l.553

**Le miroir de `find_drop`.** Sert au preset « moment calme » (`section: "calm"`)
— une vidéo posée plutôt qu'un edit d'action.

Même préparation (grille 0,25 s + lissage 2 s), puis :

```python
windows = np.lib.stride_tricks.sliding_window_view(energy, W)  # (N-W+1, W)
means = windows.mean(axis=1)
mins  = windows.min(axis=1)
```

`sliding_window_view` crée une **vue** de toutes les fenêtres possibles sans
copier les données. On calcule moyenne et minimum de chacune d'un coup.

### Le piège du silence (l.584-587)

```python
silence = 0.05 * float(energy.max())
musical = np.flatnonzero(mins >= silence)      # fenêtres sans silence
if musical.size == 0:                          # morceau très faible partout
    musical = np.arange(len(means))
```

La fenêtre à énergie moyenne minimale d'un morceau, c'est presque toujours…
**l'intro muette ou le fade final**. Techniquement la plus calme, musicalement
inutilisable.

D'où le filtre : on n'accepte que les fenêtres dont le **minimum** reste
au-dessus de 5 % du pic. On cherche « le passage doux », pas « le silence ».

Et si aucune fenêtre ne passe le filtre (morceau très faible partout), on
retombe sur toutes les fenêtres plutôt que de retourner `None` — encore une
dégradation.

---

## Attention au mot « section »

Il désigne **deux choses différentes** dans le fichier, et le code le signale
explicitement (l.39-40 et l.563-564) :

| Où | Valeurs | Sens |
|---|---|---|
| `config["section"]` | `"drop"` \| `"calm"` | **Quel passage du morceau** on cadre |
| `entry["section"]` d'une entrée d'EDL | `"buildup"` \| `"drop"` \| `"main"` | **Où se situe ce segment** dans la fenêtre |

Le premier est un réglage de preset, le second est construit par `build_edl`.

---

## Ce qui est testé

`tests/test_find_drop.py` · `tests/test_find_calm.py`

Les deux fonctions sont **pures et sans RNG** : on leur passe un `analysis`
fabriqué à la main (une courbe d'énergie en escalier, par exemple) et on vérifie
le timestamp retourné. Aucun fichier audio n'est nécessaire.

C'est ce qui permet de tester le cas « énergie plate → None » sans avoir à
produire un vrai morceau ambient.

---

## Les réglages qui touchent ce bloc

| Clé de config | Défaut | Effet |
|---|---|---|
| `section` | `"drop"` | `"calm"` bascule sur `find_calm` |
| `buildup` | `10.0` | secondes de montée gardées avant le drop ([03](03-cadrage.md)) |

Les fenêtres de ±8 s, le lissage de 2 s et le seuil de 20 % sont **en dur**.
Ce ne sont pas des réglages : ce sont les constantes qui définissent ce qu'on
appelle un drop.
