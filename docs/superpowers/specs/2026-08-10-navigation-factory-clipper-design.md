# Spec — Séparer la Video Factory du Clipper dans la navigation

**Date** : 2026-08-10
**Statut** : validé, prêt pour plan d'implémentation

## Problème / objectif

L'interface présente cinq onglets à plat dans la barre latérale : Niches,
Presets, Catalogue, Clipper, Réglages. Rien n'y dit que les trois premiers
forment **un seul outil** — la chaîne de montage beatsync, où une niche relie
un preset et une sélection du catalogue — tandis que le Clipper est un
**second outil indépendant**, avec ses propres sources et sa propre
bibliothèque.

Objectif : rendre cette séparation visible dans la navigation, en regroupant
les trois écrans de montage sous une **Video Factory** dotée de sous-onglets.

## Décisions de cadrage (issues du brainstorming)

- **Réglages reste au premier niveau**, commun aux deux outils. Écarté :
  éclater `SettingsTab.tsx` en deux (réglages de montage sous la Video
  Factory, réglages du clipper dans le Clipper) — conceptuellement plus propre,
  mais un seul endroit où chercher un réglage l'emporte ici.
- **Sous-onglets en ligne au-dessus du contenu**, pas dans la barre latérale.
  Le composant `Tabs` de shadcn est déjà dans le projet
  (`components/ui/tabs.tsx`) et sert exactement à ça ; il n'est utilisé nulle
  part aujourd'hui. Écarté : le dépliage indenté dans la barre latérale (aucun
  composant du projet ne le fait, il faudrait l'écrire) et la double colonne
  (mange de la largeur, or les cartes de niches et l'éditeur de preset sont
  déjà contraints à `max-w-4xl`).
- **Réorganisation de navigation uniquement.** Le contenu des cinq écrans,
  les composants, l'API et le back-end ne bougent pas.

## Design

### 1. Structure

| barre latérale | sous-onglets |
|---|---|
| **Video Factory** | Niches · Presets · Catalogue |
| **Clipper** | — |
| **Réglages** | — |

`Shell` porte deux états au lieu d'un : l'outil courant (`tool`) et, pour la
Video Factory, son sous-onglet (`factoryTab`). Le sous-onglet **persiste**
quand on quitte la Video Factory et qu'on y revient — sortir voir le Clipper et
revenir doit rendre l'écran qu'on avait laissé, pas un retour au premier
sous-onglet.

Icônes lucide-react, comme le reste du projet : la Video Factory reprend
`Blocks` (aujourd'hui sur Niches), le Clipper garde `Scissors`, Réglages garde
`Settings2`. Les sous-onglets n'ont pas d'icône — la barre `Tabs` est déjà
identifiée par son contexte.

### 2. Trois points qui cassent si on les oublie

**Les deep links.** `tabFromHash` rend aujourd'hui un seul onglet
(`#preset/… → presets`, sinon `niches`). Il doit désormais rendre un **couple**
`(tool, factoryTab)` : `#preset/12` ouvre la Video Factory **et** son
sous-onglet Presets, `#niche/3` la Video Factory et Niches. Sans ça, un lien
partagé vers un preset atterrit sur les Niches.

`NichesTab` et `PresetsTab` continuent de lire et d'écrire le hash eux-mêmes
(`#niche/<id>`, `#preset/<id>`) : rien à changer de leur côté.

**Le panneau des rognages en cours.** `TrimJobsPanel` est monté dans `Shell`
avec `visible={tab === "catalogue"}`, précisément pour survivre à un changement
d'onglet — un rognage dure environ deux minutes. La condition devient un test à
**deux termes** : Video Factory **et** sous-onglet Catalogue. L'oublier ferait
disparaître le journal sans arrêter le job, et déverrouillerait la ligne du
clip.

**Le nettoyage du hash.** Un changement d'onglet manuel efface le hash pour
abandonner l'élément deep-linké. Ça doit valoir aussi pour un changement de
**sous-onglet** : passer de Niches à Presets doit abandonner le `#niche/3`,
sinon le hash contredit ce qui est affiché.

### 3. Écran par défaut

Sans hash, l'application ouvre la **Video Factory sur Niches** — le
comportement actuel, inchangé.

### 4. Ce qui ne change pas

`NichesTab`, `PresetsTab`, `Catalogue`, `ClipperTab`, `SettingsTab` : aucun
changement interne. Les props qu'ils reçoivent de `Shell` sont identiques, à
l'exception du `visible` de `TrimJobsPanel`. Aucun fichier Python n'est touché,
aucun endpoint n'est ajouté.

### 5. Vérification

Le projet n'a pas de tests frontend. Critères automatiques :

- `cd frontend && npm run build` sans erreur TypeScript ;
- `uv run pytest -q` inchangé à **646** — rien ne bouge côté Python.

Vérification à l'œil, obligatoire :

1. les trois entrées de la barre latérale, et les trois sous-onglets de la
   Video Factory ;
2. un lien `#preset/<id>` ouvre bien la Video Factory **et** le sous-onglet
   Presets, `#niche/<id>` la Video Factory et Niches ;
3. quitter la Video Factory pour le Clipper puis revenir rend le sous-onglet
   qu'on avait laissé ;
4. un rognage lancé depuis le Catalogue reste suivi quand on passe au Clipper,
   et son journal réapparaît au retour sur le Catalogue.

## Hors périmètre

- Éclater `SettingsTab.tsx` entre les deux outils.
- Renommer ou déplacer des écrans existants.
- Sous-onglets pour le Clipper (il n'a qu'un écran).
- Toute modification du back-end, des endpoints ou des composants d'écran.
