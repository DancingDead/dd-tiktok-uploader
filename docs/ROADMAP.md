# Roadmap produit — « usine à vidéos » Dancing Dead

> Mise à jour issue de l'analyse marché du 2026-07-21 (TikTok comme lanceur de tendance
> vs playlists ; playbook phonk ; scène hardstyle/gym/anime). Rapport et sources en fin
> de document. Chaque item est relié à un constat sourcé.

## Direction stratégique

Le basculement **playlists → TikTok** est acquis, et la thèse de l'outil (edits **anime 9:16
beat-synchronisés** pour **hardstyle**) est directement alignée sur les couloirs gagnants
(anime + **GymTok/Zyzz** + **sons sped-up**). Le playbook de référence (phonk) confirme que
c'est **TikTok qui tire les streams**, pas l'inverse.

**Principe directeur :** l'outil a **sur-résolu la production** et **sous-résolu la
distribution soutenable + l'apprentissage**. Le goulot n'est plus « faire de plus beaux
montages » mais « alimenter une présence multi-comptes régulière, variée et **mesurée** sur
les bons couloirs ». La roadmap suit cette bascule.

## Hors périmètre (ligne éthique — inchangée)

- ❌ **Fermes de comptes « burner » / volume posting via faux comptes** (Floodify, Chaotic
  Good) : efficace mais viole les CGU TikTok (comportement inauthentique → bannissements),
  risque réputationnel, et contraire à la ligne du projet. On connaît la tactique pour savoir
  contre quoi on se bat, on ne la copie pas.
- ❌ **Publication automatique / scraping** : décision assumée (2026-07-08). La sortie reste
  une bibliothèque postée **à la main**. La roadmap **outille** cette contrainte, ne la lève pas.

---

## Backlog priorisé

### P0 — Leviers validés, effort faible (prochain lot)

**P0.1 — Variantes audio sped-up (± nightcore/slowed)**
- *Pourquoi :* ~80 % du top 100 des sons TikTok sont tempo-altérés ; un sped-up a triplé les
  streams d'un original en 1 mois. Le hardstyle (BPM élevé) est le genre idéal. `confiance haute`
- *Quoi :* par montage, produire une version accélérée (ffmpeg `atempo` sur l'audio, la vidéo
  suit) ; option de preset. Réutilise le pipeline de rendu existant.
- *Critères d'acceptation :* un lot peut générer, pour un même son, la version normale **et**
  sped-up ; le facteur de vitesse est réglable dans le preset ; reproductibilité préservée.

**P0.2 — « Hook-first » (accroche dans la 1re seconde)**
- *Pourquoi :* un micro-moment / hook 1-2 s surperforme un extrait long ; le scroll se gagne
  sur la première seconde. `confiance moyenne`
- *Quoi :* preset qui front-load l'impact/drop dès la 1re seconde (au lieu du build-up),
  s'appuie sur la détection de drop existante.
- *Critères :* en mode hook, le 1er segment tombe sur un impact ; A/B possible avec le cadrage
  build-up actuel.

**P0.3 — Anti-homogénéité d'un lot**
- *Pourquoi :* le volume ne marche que si les N posts ont l'air **différents** (mere-exposure
  sans lassitude). Aujourd'hui les variantes se ressemblent trop. `inférence`
- *Quoi :* forcer de la diversité de format dans un lot (style d'edit, position/typo du texte,
  type de plan, couloir), pas seulement seed + punchline.
- *Critères :* deux variantes d'un même lot diffèrent visiblement au-delà de la seed.

### P1 — Combler le fossé distribution (sans franchir la ligne éthique)

**P1.1 — Couche « campagne »**
- *Pourquoi :* le fossé avec les acteurs qui percent est la distribution, pas la production.
  Le seeding de base validé : rendre le son dispo (distributeur) puis poster 3-5 vidéos maison
  par contexte, 3-6 posts/sem/compte. `confiance moyenne`
- *Quoi :* associer chaque vidéo au **son promu** ; calendrier/checklist de post-à-la-main sur
  quelques **comptes owned** thématiques (gym / anime / rave) ; réutilise le statut existant
  (proposed → approved → **posted**).
- *Critères :* on peut planifier et cocher les posts par compte/son ; vue « quoi poster
  aujourd'hui ».

**P1.2 — Boucle de feedback (l'usine qui apprend)**
- *Pourquoi :* fire-and-forget aujourd'hui ; l'usine ne s'améliore pas avec les résultats.
  C'est le plus gros levier long terme. `inférence`
- *Quoi :* saisie manuelle (ou import) des vues/perf des vidéos postées → agrège par
  preset / clip / punchline / couloir → priorise ce qui convertit.
- *Critères :* un tableau « ce qui marche » par dimension ; la génération peut favoriser les
  presets/clips gagnants.

### P2 — Positionnement de scène

**P2.1 — Couloirs de contenu au-delà de l'anime**
- *Pourquoi :* hardstyle perce via gym/Zyzz + rave/festival, pas que l'anime. Dominer la
  **scène** (MIDiA) plutôt que la viralité générique. `confiance haute`
- *Quoi :* banques **gym/Zyzz** et **rave/festival** ; punchlines calibrées par couloir.

**P2.2 — Export « prêt pour créateurs » (seeding authentique)**
- *Pourquoi :* alternative propre au volume-posting : partenariats avec de **vrais**
  edit-accounts. `confiance moyenne`
- *Quoi :* exporter un lot + brief pour des créateurs UGC / edit-accounts réels.

---

## À valider (angles morts de la recherche)

- Données **hardstyle spécifiques** minces (le playbook s'appuie sur le phonk comme proxy ;
  plusieurs claims hardstyle réfutés) → valider sur le terrain avec nos propres sons.
- **Benchmark outils concurrents** (templates CapCut beat-sync, outils IA de clipping type
  Opus, marketplaces d'edits, coût/features) : couverture partielle → passe dédiée si on veut
  se situer en coût.
- **Découverte ≠ fandom** : la viralité d'un son ≠ carrière d'artiste (MIDiA : ~19 % de
  conversion découverte→streams accrus) → mesurer la conversion son → streams.

## Références

Analyse marché (méthodo `market-research` + `deep-research`, 2026-07-21) :
- Bascule playlists→TikTok : [MIDiA](https://www.midiaresearch.com/blog/music-discovery-is-not-dead-just-evolving-the-industry-needs-to-evolve-with-it), [Motive Unknown](https://networknotes.motiveunknown.com/p/how-spotifys-editorial-stranglehold)
- 84 % Billboard (auto-déclaré TikTok) : [MBW/Luminate](https://www.musicbusinessworldwide.com/tiktok-84-of-songs-that-entered-billboards-global-200-chart-in-2024-went-viral-on-our-platform-first/)
- Sped-up → streams : [Billboard](https://www.billboard.com/pro/sped-up-songs-tiktok-streaming-charts/)
- Playbook phonk : [The Conversation](https://theconversation.com/how-a-global-crisis-drift-racing-and-memphis-hip-hop-gave-us-phonk-the-music-of-the-tiktok-generation-224960), [Hunger](https://hungermag.com/editorial/meet-the-elusive-teenage-tiktokers-leading-musics-phonk-explosion)
- Volume posting / burner pages (constat, **non recommandé**) : [Billboard](https://www.billboard.com/pro/music-hottest-tiktok-marketing-strategy-burner-pages-volume/)
- Seeding de base : [music24](https://music24.com/blog/how-to-use-tiktok-to-promote-music), [vidlo](https://vidlo.video/blog/tiktok-strategy-for-musicians/)
