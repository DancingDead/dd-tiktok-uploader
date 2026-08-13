# Retrait du marquage manuel Valider / Rejeter

**Date** : 2026-08-13
**Statut** : design validé, à implémenter

## Problème

Deux boutons « Valider » / « Rejeter » existent dans l'interface :

- `frontend/src/features/niches/VideoLibrary.tsx` — sur les vidéos générées par une niche ;
- `frontend/src/features/clipper/SourceDetail.tsx` — sur les clips proposés par le Clipper.

Ils écrivent `approved` ou `rejected` dans la colonne `status` de `videos` et de
`clipper_clips`. **Rien ne consomme ce statut en aval** : c'est du marquage pur.

Trois constats mesurés sur le code avant décision :

1. `videos.status` et `clipper_clips.status` ne sont jamais écrits ailleurs que par ces
   deux boutons. `generate_niche.py` insère toujours `proposed` via `db.create_video`, et
   les valeurs `posted` et `failed` ne sont produites par aucun code du dépôt. Retirer les
   boutons fige donc le statut à `proposed` pour toujours.
2. Le paramètre `status` de `db.list_videos` n'a **aucun appelant** hors des tests. Les deux
   seuls appels réels (`webui.py:396` et `:403`) listent sans filtre.
3. Les `CHECK (status IN (...))` sont écrits dans le `CREATE TABLE` du schéma. SQLite ne sait
   pas modifier une contrainte `CHECK` : il faudrait recréer la table et recopier les lignes,
   sur une `platform.db` de production déjà peuplée de lignes `approved` / `rejected`.

## Décision

Retrait à toutes les couches **sauf le stockage**. Les colonnes et leurs `CHECK` restent en
place, inertes.

Ce choix évite une migration destructive sur la base de la tour pour un gain nul : une colonne
figée à sa valeur par défaut ne coûte ni performance ni lisibilité, là où une recréation de
table sur une base de prod est le seul geste irréversible du lot.

## Périmètre

### Backend — `webui.py`

- Suppression de l'endpoint `POST /api/videos/<int:video_id>/status` (l. 753).
- Suppression de l'endpoint `POST /api/clipper/clips/<int:clip_id>/status` (l. 1011).

Ils deviennent 404. C'est le comportement voulu : un onglet resté ouvert sur l'ancienne
interface ne doit pas continuer à écrire des statuts.

### Base — `db.py`

- Suppression de `set_video_status`.
- Suppression de `set_clipper_clip_status`.
- Suppression du paramètre `status` de `list_videos` (et de la clause `AND status = ?`).
- **Aucune modification du schéma.** Les colonnes `videos.status` et `clipper_clips.status`
  et leurs `CHECK` restent tels quels. La colonne se fige à `proposed` par défaut, et les
  lignes historiquement marquées `approved` / `rejected` restent lisibles.

### Frontend

Dans `VideoLibrary.tsx` et `SourceDetail.tsx` :

- retrait des boutons « Valider » et « Rejeter » ;
- retrait du helper `setStatus` ;
- retrait des tables `STATUS_LABEL` et `STATUS_VARIANT` et du `<Badge>` de statut — sans les
  boutons, il afficherait « à valider » sur 100 % des lignes : il n'informe plus de rien et
  occupe de la place dans une liste déjà dense ;
- retrait des imports devenus morts (`Check`, `X`, `Badge`).

L'état `busyId` / `busy` **reste** : il garde encore le bouton Supprimer contre le
double-clic.

Dans `frontend/src/lib/api.ts` :

- retrait de `setVideoStatus` et `setClipperClipStatus` ;
- les champs `status` des types `Video` et `ClipperClip` **restent** : `/api/state` continue
  de les émettre, et un type qui ne décrirait plus la réponse réelle serait pire que le champ
  inerte.

`ClipperTab.tsx` n'est **pas** touché : ses `STATUS_LABEL` / `STATUS_VARIANT` décrivent les
statuts de **source** (`pending` / `transcribing` / `analyzing` / `rendering` / `done` /
`failed`), qui eux sont bien écrits par le pipeline `clip_source.py` et pilotent l'affichage
d'avancement.

### Ce qui reste dans les deux listes

Lecture (`GET /api/videos/<id>`, `GET /api/clipper/clips/<id>`), téléchargement (`?dl=1`) et
suppression (`DELETE`). Rien d'autre ne disparaît.

## Tests

### Tests existants à retirer ou réécrire

- `tests/test_db.py:202-204` — filtre `list_videos(status=...)` ;
- `tests/test_db.py:228-229` — `set_video_status` ;
- `tests/test_db.py:282-283` — `set_clipper_clip_status` ;
- `tests/test_clipper_api.py:130-132` et `:175` — endpoint de statut de clip.

### Non-régression à ajouter avant merge

- `POST /api/videos/<id>/status` rend **404** (l'endpoint n'existe plus).
- `POST /api/clipper/clips/<id>/status` rend **404**.
- La bibliothèque de vidéos d'une niche reste listable via `/api/state` et supprimable via
  `DELETE /api/videos/<id>` — le chemin de suppression ne passe plus par la colonne.
- La liste de clips d'une source reste listable et supprimable de la même façon.
- Le build TypeScript passe : avec `noUnusedLocals`, un import laissé derrière est une
  erreur de compilation, pas un avertissement.

## Risque

Nul côté données : aucune écriture, aucune migration, aucune suppression de colonne. Le seul
geste irréversible envisageable — le retrait des `CHECK` — est explicitement écarté.

Si un cycle de validation devait revenir plus tard, la colonne est toujours là et les deux
endpoints se réécrivent en quelques lignes.

## Hors périmètre

- Toute modification du schéma SQLite.
- Le statut des **sources** du Clipper (`clipper_sources.status`), qui pilote l'affichage
  d'avancement et reste entièrement en place.
- Les valeurs `posted` et `failed`, qu'aucun code n'écrit aujourd'hui et que ce changement
  ne rend ni plus ni moins mortes.
