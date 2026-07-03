# Design V4 — fin musicale, cadrage intelligent, accents « CapCut »

Date : 2026-07-04 — validé en session avec Théo. Complète les specs V1/V2.
Backup de la V3 : commit `023eba1`, tag `v3-backup`.

## Retours à l'origine

1. La musique coupait en plein milieu d'une phrase.
2. Le crop central faisait sortir des personnages du cadre.
3. Envie d'un rendu « CapCut » (gros effets), avec priorité au propre.

## #1 — Fin sur phrase (choix : sèche, sans fade)

`snap_end_to_phrase` (pure, testée) : la fin de fenêtre est étendue au
prochain multiple de 16 beats après le drop (période = médiane des écarts de
beats), retombe au multiple précédent si ça dépasse le morceau. La durée
devient variable (~28-36 s pour 30 s demandées).

## #2 — Cadrage (choix : les trois stratégies)

Scan enrichi par frame : `interest_x` (centroïde des visages pondéré par
surface, sinon centroïde des contours), `dual` (duel = 2 visages aux deux
bords, détection STRICTE minNeighbors=5 — le réglage permissif voit des
visages dans les falaises), logo coin haut-gauche masqué avant détection.

`build_edl` par extrait (fenêtre d'au moins 3 échantillons / 1,5 s) :
- `focus_x` = intérêt moyen → le crop 9:16 se cale dessus (`layout: crop`) ;
- duel majoritaire → `layout: split` (moitiés G/D empilées haut/bas, 1080x960) ;
- dispersion σ ≥ 0.18 → `layout: blur` (plan entier sur fond flouté-assombri).

`delogo` (config, défaut on) : la zone du logo de chaîne est gommée à la
source — le recadrage ou le fond flouté peuvent la faire entrer au champ.

## #3 — Accents (choix : RGB + glitch + titre, version V3 en backup)

- `rgb` : rgbashift ±8 px, 3 premières frames des impacts (drop + flashs).
- `glitch` : rgbashift fort (±14 px), 2 frames, ~25 % des segments intenses
  du drop (tirage seedé).
- `--title` : drawtext sobre (Helvetica, blanc bordé) pendant le buildup,
  disparaît à l'impact du drop. Police macOS ; ignoré si absente.

Tout est débrayable : `accents`, `effects`, `delogo`, `chrono` dans la config.

## Validation

65 tests purs ; contrôle visuel frame par frame sur le rendu réel (titre,
flash+RGB à l'impact, recadrage, blur sans logo) ; reproductibilité à
l'octet près vérifiée à chaque itération. Deux ratés attrapés et corrigés
en validation : faux duels sur textures (→ détection stricte), logo visible
en layout blur (→ delogo).
