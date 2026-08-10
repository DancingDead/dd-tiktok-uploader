# Spec — Rogner le début et la fin d'un clip du catalogue

**Date** : 2026-08-10
**Statut** : validé, prêt pour plan d'implémentation

## Problème / objectif

Les clips du catalogue partagé `clips/` sont importés depuis YouTube via
`fetch_tracks.py --video`. Beaucoup portent une **intro** (logo de chaîne,
générique) et une **outro** (cartons d'abonnement, écran de fin) que rien ne
permet de retirer aujourd'hui. `build_edl` peut donc y piocher un extrait, et
le montage se retrouve avec un logo de chaîne au milieu d'une vidéo du label.

Objectif : depuis l'onglet Catalogue, **couper le début et la fin d'un clip**,
en voyant ce qu'on coupe.

## Mesures qui fondent la conception

Relevées sur le catalogue réel (11 clips) :

- **Les images-clés sont très irrégulières.** Sur un clip AV1 :
  `0 → 5,07 → 5,42 → 6,27 → 10,13 → 15,20 s`. Sur un H.264 :
  `0 → 2,38 → 4,13 → 5,07 → 5,87 s`. Une copie de flux ne pouvant couper que
  sur ces points, elle laisserait jusqu'à 5 s d'intro — ou en couperait 10.
  **Inutilisable pour cet usage**, d'où le réencodage.
- **8 clips sur 11 sont en AV1**, codec lent à décoder. `scan_clips` et
  `render` les redécodent à chaque génération.

## Décisions de cadrage (issues du brainstorming)

- **Coupe destructive** : le fichier du catalogue est réécrit, rogné. Écarté :
  mémoriser des bornes et laisser le montage les respecter — ça obligerait
  `load_clips`, le scan et son cache à en tenir compte, donc à toucher au cœur
  du montage pour une fonctionnalité de catalogue. Une intro est indésirable
  pour toutes les niches, donc la partager n'a pas de sens.
- **Réencodage vers H.264**, pas vers le codec source. Bénéfice secondaire
  assumé : les clips AV1 deviennent du H.264, et tout le pipeline (scan +
  rendu) y gagne à chaque génération. Écarté : le mode hybride
  copie-si-ça-tombe-juste / réencodage-sinon, dont le résultat dépendrait du
  fichier — difficile à expliquer et à tester.
- **Bornes posées à la main**, avec le lecteur d'aperçu déjà en place. Pas de
  détection automatique d'intro : ce serait un autre chantier, et il ne vaut
  pas le coup tant que les clips sont importés un par un.
- **Irréversible, et assumé.** Le lien YouTube reste dans `clip_links.txt`,
  donc un clip mal rogné se réimporte — au prix d'un téléchargement.

## Design

### 1. Un script `trim_clip.py`

Sur le moule des autres scripts indépendants du projet (`fetch_tracks.py`,
`generate_niche.py`, `clip_source.py`) : logique pure testable au centre, appel
FFmpeg isolé.

```
uv run python trim_clip.py <nom-du-clip> <début> <fin> [<racine>]
```

**Lancé en tâche de fond** par l'interface via le `start_job` existant :
réencoder un clip AV1 de trois à quatre minutes prend plusieurs minutes. Le
suivi passe par le `JobLog` déjà utilisé pour les téléchargements et la
génération.

| fonction | rôle | nature |
|---|---|---|
| `coerce_bounds(start, end, duration)` | valide et borne, lève `ValueError` sinon | **pure** |
| `ffmpeg_trim_args(source, target, start, end)` | arguments FFmpeg | **pure** |
| `trim_clip(path, start, end, log)` | écrit le temporaire, remplace | I/O |

### 2. Trois points de conception qui comptent

**Écriture atomique.** FFmpeg écrit dans un fichier temporaire placé dans le
même dossier (donc sur le même volume, pour que le remplacement soit un simple
`rename`), et le remplacement n'a lieu qu'après un code retour nul. Sans ça,
un rendu interrompu — coupure, `Ctrl-C`, disque plein — détruirait le clip au
lieu de le rogner. Le temporaire est effacé en cas d'échec.

**Le nom de fichier ne change pas.** Les niches référencent leurs clips par nom
(`clips/xxx.mp4`, colonne `clips` de la table `niches`) : les sélections
survivent au rognage sans qu'on touche à la base.

**Le cache de scan se périme tout seul.** `scan_clips` indexe par md5 du chemin
et invalide sur la date de modification ; la réécriture la change. Rien à
faire — mais il faut l'écrire, sinon quelqu'un ajoutera une invalidation
manuelle inutile.

### 3. Encodage

| paramètre | valeur | pourquoi |
|---|---|---|
| codec vidéo | `libx264` | coupe précise à la frame, et décodage bien plus rapide que l'AV1 pour le scan et le rendu |
| `crf` | **18** | un cran au-dessus du CRF 20 du rendu final : ce clip est un intermédiaire qui sera réencodé une seconde fois par le montage, lui laisser de la marge évite d'empiler deux générations de perte |
| `preset` | `medium` | même valeur que le rendu du projet |
| audio | `-c:a copy` | beatsync ne lit jamais l'audio d'un clip, mais détruire une donnée dont on n'a pas besoin n'est pas une raison de la détruire. `copy` est un no-op quand il n'y a pas de piste |
| flags | `bitexact` | invariant du projet : sans eux, l'encodeur date le fichier |

`-ss` et `-to` sont placés **avant** `-i` (seek d'entrée), comme dans
`clipper.render_clip`.

### 4. Validation

`coerce_bounds` est pure et lève `ValueError` :

- bornes non convertibles en nombre ;
- `start < 0`, `end > duration`, `start >= end` ;
- durée résultante inférieure à `MIN_TRIMMED_DUR` (**1,0 s**) — en dessous, le
  clip n'a plus de matière exploitable et `usable_intervals` le rejetterait de
  toute façon.

Côté endpoint : 400 sur `ValueError`, **jamais 500**. Garde anti-traversal sur
le nom de fichier, identique à celle de `_delete_asset` : uniquement un fichier
directement sous `clips/`. L'extension doit appartenir à **`VIDEO_EXTS`** et
non à `CLIP_EXTS` — ce dernier contient aussi les images, et rogner un `.jpg`
n'a pas de sens (une image est montée en flash court, sans notion de durée).

### 5. Endpoint

```
POST /api/clips/<name>/trim   {"start": float, "end": float}
```

Rend `{"job_id": …}` ou 400/404/409. Le job est nommé **`trim-<nom du clip>`**
et non `trim` : `start_job` refuse un second job de même nom, donc un nom global
interdirait de rogner deux clips différents en parallèle alors que rien ne s'y
oppose. Le 409 protège du double-clic sur le **même** clip, ce qui ferait
travailler deux FFmpeg sur le même fichier temporaire.

La durée du clip est lue côté serveur par `ffprobe` — on ne fait pas confiance
à celle envoyée par le client pour valider les bornes.

### 6. Interface

Dans l'onglet **Catalogue**, section Clips, une action « Rogner » (icône
ciseaux lucide-react) sur chaque clip, à côté des actions existantes. Elle
ouvre une modale contenant :

- le lecteur déjà servi par `GET /api/clips/<name>` ;
- deux champs de temps (début, fin), en secondes avec décimale ;
- un bouton **« prendre ici »** à côté de chacun, qui y recopie la position
  courante du lecteur (`video.currentTime`) — c'est ce qui rend l'opération
  praticable sans noter des timecodes ailleurs ;
- la **durée résultante** affichée en clair sous les champs, recalculée à
  chaque saisie ;
- un avertissement que l'opération est **irréversible**, et une confirmation
  explicite — pas un simple bouton.

Pendant le job, le `JobLog` existant ; à la fin, rafraîchissement de l'état.

### 7. Tests

| fichier | couvre |
|---|---|
| `tests/test_trim_clip.py` | `coerce_bounds` : bornes inversées, négatives, au-delà de la durée, durée résultante trop courte, valeurs non numériques, cas nominal. `ffmpeg_trim_args` : `-ss`/`-to` avant `-i`, codec et CRF, `-c:a copy`, flags `bitexact`, chemin du temporaire |
| `tests/test_webui_platform.py` (étendu) | 400 sur bornes invalides, 404 sur clip inconnu, anti-traversal sur le nom, refus d'une image, job lancé sur bornes valides |

L'appel FFmpeg lui-même et le remplacement atomique ne sont pas testés
automatiquement, comme les autres I/O du projet. **Vérification manuelle
obligatoire** sur un vrai clip AV1 du catalogue : durée résultante correcte,
codec H.264 en sortie, fichier lisible, et le clip toujours sélectionnable dans
sa niche.

## Hors périmètre

- Détection automatique d'intro ou d'outro.
- Annulation d'un rognage (le clip est réimportable depuis `clip_links.txt`).
- Coupe au milieu d'un clip (seuls le début et la fin sont rognés).
- Rognage des sons du catalogue `tracks/` — les morceaux du label n'ont pas
  d'intro à retirer.
- Rognage des sources du clipper (`data/clipper/`), qui relèvent d'un autre
  sous-système.
