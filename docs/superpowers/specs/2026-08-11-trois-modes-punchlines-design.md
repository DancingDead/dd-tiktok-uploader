# Trois modes de texte incrusté

Date : 2026-08-11
État : validé, prêt à planifier

## Le besoin

Le texte incrusté n'a aujourd'hui que deux modes : des punchlines générées qui
changent à chaque coupe, ou un texte fixe écrit à la main. Il manque le cas
intermédiaire, qui est celui dont Théo a besoin pour trier un lot : **une seule
punchline générée, stable du début à la fin de la vidéo**.

Son intérêt tient au lot, pas à la vidéo isolée. Générer N variantes d'une niche
donne alors N vidéos portant chacune UNE punchline, différente d'une variante à
l'autre. On choisit à l'œil celle qui marche, au lieu de juger un texte qui
défile.

## Les trois modes

| Mode (valeur stockée) | Libellé UI | Texte affiché |
|---|---|---|
| `llm` (défaut, existant) | Punchlines générées | Change à chaque créneau de coupe |
| `llm_unique` (**nouveau**) | Une punchline générée | Une seule, générée, du début à la fin |
| `fixe` (existant) | Texte fixe | Une seule, écrite à la main, du début à la fin |

Les trois s'excluent : `subtitles.mode` porte une de ces trois valeurs.

### Pourquoi un troisième mode et non une option du mode `llm`

L'alternative écartée était une case « garder la même punchline toute la vidéo »
sous le mode `llm`. Elle rend l'état produit de deux réglages (mode × case) là où
l'utilisateur décrit trois choix exclusifs, et la case n'a aucun sens en mode
`fixe` — il faudrait la masquer, donc réintroduire la complexité qu'on prétendait
éviter. Trois valeurs pour trois choix.

## Ce qui change dans le moteur

`apply_subtitles` (beatsync.py) gagne une branche. Les modes `fixe` et
`llm_unique` partagent le même chemin — **une caption unique posée sur toutes les
entrées de l'EDL, sans créneaux ni `min_dur`** — et ne diffèrent que par la
provenance du texte :

- `fixe` : `subtitles.text`, saisi à la main.
- `llm_unique` : `generate_punchlines(preprompt, count=1, seed, cache_dir, model)`,
  dont on prend le premier élément.

Rien d'autre ne bouge : ni `assign_caption_slots`, ni `generate_punchlines`, ni
`_caption_filter`, ni le rendu.

### La variété entre variantes est déjà acquise

C'est le point qui rend cette fonctionnalité presque gratuite, et il mérite d'être
écrit parce qu'il n'est pas évident : **aucun code n'est nécessaire pour que deux
variantes d'un lot portent deux punchlines différentes.**

`generate_niche.plan_variants` attribue déjà une seed distincte à chaque variante.
Cette seed entre à la fois dans la clé de cache de `generate_punchlines`
(`backend|model|preprompt|count|seed`) et dans le texte du prompt lui-même
(`_punchline_user_prompt` : « Variation n°{seed} »), pour les deux backends — LM
Studio la reçoit en plus comme paramètre d'API. Cinq variantes valent donc cinq
appels distincts.

Vérifié dans le code au moment de la conception, pas supposé.

### La bibliothèque affiche la punchline sans rien coder

`generate_video` retourne `captions` = l'**ensemble dédoublonné** des textes de
l'EDL, que `generate_niche` stocke dans `subtitles.lines` de la vidéo. En mode
`llm_unique`, cet ensemble contient exactement un élément, et `VideoLibrary`
l'affiche déjà sous la vignette. C'est précisément ce qu'il faut pour comparer les
variantes d'un lot — et ça tombe juste sans modification.

## Ce qui change ailleurs

**Validation serveur** (webui.py) : `ALLOWED_SUBTITLE_MODES` passe de
`{"llm", "fixe"}` à `{"llm", "llm_unique", "fixe"}`. `coerce_subtitles` continue
de refuser toute autre valeur en 400.

**UI** (`NicheDetail.tsx`) : trois boutons radio au lieu de deux. La zone
« Consigne de style » s'affiche pour `llm` **et** `llm_unique` ; la zone de texte
libre pour `fixe` seul. Le type `mode` de `lib/api.ts` gagne la valeur.

**Réglages de placement** : position horizontale, position verticale et taille
restent **communs aux trois modes**, comme aujourd'hui. Décision explicite : pas
de réglages mémorisés par mode, donc aucun champ ajouté en base et aucune
migration. Pas de réglage d'alignement non plus — les deux curseurs de position
suffisent, le texte étant centré sur le point choisi.

**Base de données** : aucun changement de schéma. `subtitles` est un blob JSON.

## Compatibilité

Les niches existantes portent `llm` ou `fixe` et se comportent à l'identique. Le
défaut de `DEFAULT_CONFIG` reste `llm`. Aucune migration.

## Comportement en cas d'échec

Si le LLM est injoignable ou répond mal, `generate_punchlines` rend `[]` — la
caption devient une chaîne vide, et la vidéo sort **sans texte incrusté** plutôt
que d'échouer. C'est déjà la règle du mode `llm` : l'usine ne bloque jamais sur le
LLM. Le mode `llm_unique` n'y déroge pas.

Conséquence à assumer : un lot généré avec LM Studio éteint rend N vidéos muettes
de texte, sans erreur visible autre que l'absence de punchline dans la
bibliothèque.

## Tests

Purs, sur `apply_subtitles` avec un `generate_punchlines` mocké :

1. En `llm_unique`, **tous** les segments de l'EDL portent la même caption.
2. Le LLM est appelé **une seule fois**, avec `count=1` — et non une fois par
   créneau.
3. Deux seeds différentes produisent deux captions différentes (c'est la promesse
   du mode : un lot de N variantes donne N punchlines).
4. Un LLM en échec (`[]`) rend une caption vide sur tous les segments, sans lever.
5. `assign_caption_slots` n'est pas appelé (pas de créneaux dans ce mode).
6. `coerce_subtitles` accepte `llm_unique` et refuse toujours une valeur inconnue.

Chaque test doit tomber si l'on retire la ligne qu'il prétend couvrir — à vérifier
par mutation, comme sur les corrections récentes.

## Hors périmètre

- Réglages de placement mémorisés par mode.
- Alignement gauche/centre/droite du bloc de texte.
- Apparition/disparition de la punchline sur une portion de la vidéo : en
  `llm_unique`, le texte est affiché du début à la fin, comme en mode `fixe`.
- Choix de la punchline parmi plusieurs propositions : on demande une punchline,
  on la prend.
