# Recette bout-en-bout — avant ouverture à l'équipe

**Environnement :** la tour Windows (prod), via `https://dancingdeadhq.tail2611ce.ts.net/`
ou `http://dancingdeadhq:8765` en Tailscale. Pas en local : on teste ce que l'équipe
utilisera vraiment, LM Studio headless et session 0 compris.

**Critère de sortie :** un membre autre que Théo peut se connecter, créer sa niche,
générer un lot et récupérer ses vidéos **sans aide**.

**Mode d'emploi :** dérouler les blocs dans l'ordre — ils se suivent (R2 alimente R5,
R5 alimente R6). Cocher ce qui passe, remplir « Constaté » sinon, et reporter chaque
écart dans [Anomalies](#anomalies) avec un numéro `A<n>`.

**Avant de commencer, sur la tour :**

```powershell
cd "C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader"
git pull
cd frontend ; npm run build ; cd ..
# puis redémarrer la tâche DD-Usine (le build est servi par Flask)
```

> Le correctif d'upload (`fix/upload-un-seul-bouton`) n'est actif qu'après ce build.

---

## R0 — Admin : créer un compte membre

Rien de tout le reste n'est testable sans ça, et c'est la seule étape en ligne de
commande. À faire sur la tour, en PowerShell, dans le dossier du dépôt.

### R0.1 — Créer un membre
- [ ] `uv run python db.py add-member <prenom>`
- Attendu : la saisie du mot de passe est **masquée** (aucun caractère affiché),
  puis « membre ajouté : \<prenom\> »
- Constaté :

### R0.2 — Lister les membres
- [ ] `uv run python db.py list-members`
- Attendu : le nouveau membre apparaît ; aucun mot de passe ni hash n'est affiché
- Constaté :

### R0.3 — Nom déjà pris
- [ ] Relancer `add-member` avec le **même** prénom
- Attendu : un refus explicite (le nom est unique en base), pas une trace Python brute
- Constaté :

### R0.4 — Changer un mot de passe
- [ ] `uv run python db.py set-password <prenom>`, puis tenter de se connecter avec
      l'**ancien** mot de passe
- Attendu : l'ancien est refusé, le nouveau fonctionne
- Constaté :

### R0.5 — Mot de passe solide
- [ ] Vérifier que le mot de passe choisi n'est pas devinable
- Attendu : le login est **exposé publiquement** via Funnel et **sans limitation de
  tentatives** — un mot de passe faible est une porte ouverte. Aucun garde-fou logiciel
  ne le vérifie : c'est une discipline, pas une fonctionnalité.
- Constaté :

---

## R1 — Connexion

À faire **depuis un autre poste que la tour** (ou au minimum un autre navigateur, en
navigation privée), avec le compte créé en R0.

### R1.1 — Connexion nominale
- [ ] Ouvrir l'URL, saisir membre + mot de passe, valider (clic ou touche Entrée)
- Attendu : l'app s'ouvre sur l'onglet Niches, le prénom s'affiche en haut à gauche
- Constaté :

### R1.2 — Mauvais mot de passe
- [ ] Bon membre, mauvais mot de passe
- Attendu : « identifiants invalides » sous le formulaire. Le message ne doit **pas**
  distinguer « membre inconnu » de « mot de passe faux » (sinon il confirme quels
  prénoms existent)
- Constaté :

### R1.3 — Membre inexistant
- [ ] Un prénom qui n'existe pas
- Attendu : exactement le même message qu'en R1.2
- Constaté :

### R1.4 — Persistance de session
- [ ] Fermer l'onglet, rouvrir l'URL
- Attendu : toujours connecté, sans ressaisir le mot de passe
- Constaté :

### R1.5 — Déconnexion
- [ ] Cliquer son prénom en haut à gauche → « Déconnexion »
- Attendu : retour à l'écran de login ; recharger la page ne reconnecte pas
- Constaté :

### R1.6 — Accès direct sans session
- [ ] Déconnecté, ouvrir une URL profonde (par ex. `…/#preset/1`)
- Attendu : l'écran de login, pas un écran vide ni une erreur technique
- Constaté :

---

## R2 — Catalogue : les sons

Onglet **Catalogue**, sous-onglet **Sons**. C'est le premier écran où un nouveau
membre agit — le bug corrigé pendant la conception de cette recette était ici.

### R2.1 — Uploader un son depuis son ordinateur
- [ ] Cliquer « Uploader un fichier »
- Attendu : l'explorateur de fichiers s'ouvre **immédiatement** ; après avoir choisi un
  mp3, l'envoi part **seul** (aucun second clic), le bouton affiche « Envoi… » et se
  désactive, puis toast « uploadé » et le fichier apparaît dans le tableau avec sa taille
- Constaté :

### R2.2 — Ré-uploader le même fichier
- [ ] Refaire R2.1 avec exactement le même fichier
- Attendu : l'envoi repart (le champ est réinitialisé entre deux essais) — pas un
  bouton inerte
- Constaté :

### R2.3 — Format non supporté
- [ ] Uploader un `.txt` ou un `.pdf`
- Attendu : message d'erreur nommant le fichier, rien n'est ajouté au tableau
- Constaté :

### R2.4 — Ajouter un lien YouTube
- [ ] Coller une URL de vidéo dans le champ, cliquer « Ajouter » (ou touche Entrée)
- Attendu : le lien s'affiche dans la liste dessous, toast « lien ajouté »
- Constaté :

### R2.5 — Télécharger les sons
- [ ] Cliquer « Télécharger les sons »
- Attendu : un journal apparaît et défile, puis « téléchargement terminé » ; le mp3
  arrive dans le tableau. Noter le **temps** que ça prend.
- Constaté :

### R2.6 — Lien invalide
- [ ] Ajouter une URL bidon (vidéo privée, ou texte quelconque) et lancer le téléchargement
- Attendu : l'échec est **annoncé** (toast « échec — voir le journal ») et lisible dans
  le journal ; l'app ne reste pas bloquée sur « en cours »
- Constaté :

### R2.7 — Retirer un lien
- [ ] Icône corbeille à droite d'un lien de la liste
- Attendu : le lien disparaît, toast « lien retiré ». Cela n'efface **pas** le mp3 déjà
  téléchargé.
- Constaté :

### R2.8 — Repérer un son dans un gros catalogue
- [ ] Avec une vingtaine de sons dans le tableau, chercher un fichier précis
- Attendu : le tableau est trié par nom et lisible. Noter qu'il n'y a **ni champ de
  recherche ni aperçu** dans le Catalogue (ils n'existent que dans les cartes de
  sélection d'une niche) : juger si ça tient à l'échelle réelle du catalogue du label.
- Constaté :

### R2.9 — Supprimer un son du catalogue
- [ ] Icône corbeille dans la ligne du tableau
- Attendu : une **confirmation** s'affiche, précisant que le fichier sera effacé du
  disque et retiré des niches qui l'utilisent ; après validation, toast « supprimé »
- Constaté :

---

## R3 — Catalogue : les clips

Sous-onglet **Clips**. Même composant que R2 : si un comportement diverge entre les
deux sections, c'est une anomalie en soi.

### R3.1 — Uploader un clip
- [ ] Même parcours que R2.1 avec un `.mp4`
- Attendu : identique à R2.1. Tester avec un **gros fichier** (≥ 200 Mo) : c'est le cas
  réel, et c'est là qu'on verra une éventuelle limite de taille ou un timeout du Funnel.
- Constaté :

### R3.2 — Import YouTube en vidéo
- [ ] Ajouter un lien d'épisode/AMV, cliquer « Télécharger les clips »
- Attendu : le fichier arrive en mp4 ≤ 1080p dans le tableau des clips (et **pas** dans
  les sons). C'est le chemin d'alimentation réel de la banque de rushes.
- Constaté :

### R3.3 — Voir ce que contient un clip
- [ ] Depuis le Catalogue, essayer de visionner un clip avant de le sélectionner
- Attendu : ce n'est **pas possible** ici — l'aperçu n'existe que dans la carte « Clips
  de la niche » (R5.4). Juger si un membre qui importe 30 rushes peut travailler sans
  aperçu au niveau du catalogue.
- Constaté :

### R3.4 — Supprimer un clip utilisé par une niche
- [ ] Supprimer un clip qui est sélectionné dans une niche (à refaire après R5)
- Attendu : après confirmation, il disparaît du catalogue **et** de la sélection de la
  niche, sans casser la niche
- Constaté :

---

## R4 — Presets

Onglet **Presets**.

### R4.1 — Créer un preset depuis un modèle
- [ ] « Partir d'un modèle : … », nommer le preset, enregistrer
- Attendu : le preset apparaît dans la liste, réutilisable dans une niche
- Constaté :

### R4.2 — Régler les leviers
- [ ] Parcourir les sections : Rythme, Effets, Accents, Cadrage & contenu, Ambiance
      couleur, Police, Moment de la track (fort/calme)
- Attendu : chaque réglage est compréhensible **sans connaître le code**. Noter tout
  libellé obscur — c'est le principal risque pour un membre non technique.
- Constaté :

### R4.3 — Valeur hors plage
- [ ] Mettre « Présence personnages min » à `5`, et « Coupe tous les N beats » à `0`
- Attendu : refus ou correction automatique, avec un message. Une `min_presence` trop
  haute ne retient plus aucun clip → montage vide : c'est borné côté serveur, vérifier
  que l'UI le dit aussi.
- Constaté :

### R4.4 — Champ texte inattendu
- [ ] Saisir des lettres dans un champ numérique
- Attendu : refus propre (400 « données invalides »), pas une page cassée
- Constaté :

### R4.5 — Supprimer un preset lié à une niche
- [ ] Supprimer un preset utilisé par une niche existante
- Attendu : comportement **explicite** — soit refus motivé, soit suppression avec
  avertissement. Vérifier ensuite que la niche est toujours générable.
- Constaté :

---

## R5 — Niches

Onglet **Niches**. C'est ici qu'un membre construit son univers.

### R5.1 — Créer une niche
- [ ] « Nouvelle niche », lui donner un nom **avec accents et espaces**
      (par ex. « Gym été »)
- Attendu : création OK, le dossier est créé côté serveur avec un slug translittéré
  (`gym-ete`), pas de caractère exotique dans les chemins
- Constaté :

### R5.2 — Sélectionner des sons
- [ ] Carte « Sons de la niche » : ajouter deux ou trois morceaux du catalogue
- Attendu : ajout **immédiat** (pas de bouton « enregistrer » à trouver), toast de
  confirmation, recherche par nom fonctionnelle
- Constaté :

### R5.3 — Retirer un son
- [ ] « Retirer de la niche » sur un son sélectionné
- Attendu : il quitte la sélection mais **reste** dans le catalogue (vérifier dans
  l'onglet Catalogue)
- Constaté :

### R5.4 — Sélectionner des clips
- [ ] Carte « Clips de la niche » : ajouter au moins 5 clips
- Attendu : identique à R5.2. Avec moins de clips, les variantes se ressembleront.
- Constaté :

### R5.5 — Préprompt de punchlines
- [ ] Renseigner « Consigne de style » (par ex. « motivation gym, français, percutant,
      4 mots max »)
- Attendu : le champ est compris sans explication ; l'exemple affiché aide
- Constaté :

### R5.6 — Légende, hashtags, presets liés
- [ ] Remplir « Légende du post », « Hashtags », cocher un ou deux « Presets de montage
      liés (alternés) », enregistrer
- Attendu : toast « niche enregistrée » ; recharger la page conserve tout
- Constaté :

### R5.7 — Générer sans son sélectionné
- [ ] Retirer tous les sons, regarder la carte Génération
- Attendu : le bouton « Générer » est **désactivé** et un message rouge dit précisément
  quoi sélectionner. Pas de clic possible qui échouerait silencieusement.
- Constaté :

### R5.8 — Générer sans clip sélectionné
- [ ] Même chose côté clips
- Attendu : identique à R5.7, message mentionnant les clips
- Constaté :

### R5.9 — Supprimer une niche
- [ ] Supprimer une niche de test contenant des vidéos
- Attendu : confirmation explicite ; après coup, ni la niche ni ses vidéos ne subsistent
- Constaté :

---

## R6 — Génération et bibliothèque

Le cœur du produit. Sur une niche correctement remplie (R5).

### R6.1 — Lancer un lot de 3
- [ ] Mettre `3` variantes, cliquer « Générer »
- Attendu : toast « génération de 3 variante(s) lancée », le journal défile
- Constaté :

### R6.2 — Durée
- [ ] Chronométrer le lot complet
- Attendu : à noter, sans jugement — c'est la donnée qui dira si un membre peut lancer
  un lot et attendre, ou s'il faut le prévenir. **Temps mesuré :**
- Constaté :

### R6.3 — Fin de lot
- [ ] Attendre la fin
- Attendu : toast « N nouvelle(s) vidéo(s) ajoutée(s) à la bibliothèque », les vignettes
  apparaissent (pas de rectangle gris)
- Constaté :

### R6.4 — Les variantes sont vraiment différentes
- [ ] Comparer les 3 : seed affichée, punchlines en aperçu, morceau
- Attendu : trois seeds distinctes, des punchlines distinctes, et visuellement trois
  montages qui ne se ressemblent pas
- Constaté :

### R6.5 — Lecture dans le navigateur
- [ ] Lire une vidéo depuis la bibliothèque
- Attendu : lecture fluide, format 9:16, punchline lisible et nette, son synchronisé
  avec l'image **jusqu'à la fin** (la dérive audio/vidéo se voit sur les dernières secondes)
- Constaté :

### R6.6 — Téléchargement et lecture sur téléphone
- [ ] Télécharger une vidéo, la transférer sur un téléphone, la lire en plein écran
- Attendu : c'est le test qui compte — cadrage correct, texte non coupé par l'interface
  TikTok, qualité acceptable
- Constaté :

### R6.7 — Valider / Rejeter
- [ ] « Valider » sur une vidéo, « Rejeter » sur une autre
- Attendu : le badge de statut change immédiatement, l'action est réversible
- Constaté :

### R6.8 — Supprimer définitivement
- [ ] Icône corbeille rouge d'une vidéo
- Attendu : confirmation, puis disparition de la vignette **et** du fichier sur disque.
  Vérifier qu'on ne confond pas « Rejeter » (réversible) et « Supprimer » (définitif).
- Constaté :

### R6.9 — LM Studio éteint
- [ ] Arrêter le daemon LM Studio, relancer un lot de 1
- Attendu : la vidéo **est produite quand même**, sans punchline. L'usine ne doit jamais
  se bloquer sur le LLM. Vérifier que le membre comprend pourquoi il n'y a pas de texte.
- Constaté :

### R6.10 — Relancer une niche déjà en cours
- [ ] Pendant qu'un lot tourne, recliquer « Générer » sur la **même** niche
- Attendu : refus explicite (« un job … tourne déjà »), pas deux lots concurrents
- Constaté :

### R6.11 — Échec de génération
- [ ] Provoquer un échec (par ex. sélectionner un seul clip très court, ou un fichier
      audio corrompu)
- Attendu : « La génération a échoué — voir le journal ci-dessus ». Surtout **pas** un
  « terminé » mensonger.
- Constaté :

---

## R7 — Multi-membres et réglages

Le bloc qu'on ne peut pas faire seul, et celui qui décide de l'ouverture. **Deux
personnes connectées en même temps, sur deux postes.**

### R7.1 — Deux générations en parallèle
- [ ] Chacun lance un lot sur **sa** niche, en même temps
- Attendu : les deux partent (le verrou est par niche, pas global). Observer la tour :
  temps de rendu, charge GPU, saturation éventuelle. **Ce qu'on cherche à savoir : est-ce
  tenable, ou faut-il une règle d'usage « un lot à la fois » ?**
- Constaté :

### R7.2 — Édition concurrente de la liste de liens
- [ ] A et B ouvrent le Catalogue ; A ajoute un lien, B ajoute un autre lien sans
      recharger sa page
- Attendu : à vérifier — la liste de liens est un **fichier global partagé**, le second
  enregistrement peut écraser le premier. Noter précisément ce qui se passe.
- Constaté :

### R7.3 — Visibilité croisée
- [ ] B regarde la liste des niches
- Attendu : **B voit les niches de A** et peut les modifier ou les supprimer. Il n'y a
  aucun cloisonnement entre membres (voir [Décisions](#décisions-à-trancher)). Confirmer
  que c'est bien ce qu'on veut avant d'ouvrir.
- Constaté :

### R7.4 — Suppression d'un asset partagé
- [ ] B supprime du catalogue un son que A a sélectionné dans sa niche
- Attendu : le son disparaît de la niche de A sans prévenir A. Vérifier que la niche de
  A reste générable.
- Constaté :

### R7.5 — Réglages globaux
- [ ] A modifie l'onglet Réglages, B recharge sa page
- Attendu : les réglages sont **globaux** — A vient de changer les défauts de tout le
  monde. Rien ne le signale dans l'UI. Confirmer que c'est acceptable.
- Constaté :

### R7.6 — Un membre seul, sans aide
- [ ] Faire dérouler à un membre le parcours complet (login → catalogue → niche →
      génération → téléchargement) **sans lui expliquer**, en observant sans intervenir
- Attendu : il y arrive. Noter chaque hésitation, chaque question posée : c'est la vraie
  mesure du critère de sortie.
- Constaté :

---

## Décisions à trancher

Ce ne sont pas des bugs mais des choix de conception que la recette met sous les yeux.
À arbitrer **avant** l'ouverture, pas après.

| # | Sujet | Constat | Décision |
|---|---|---|---|
| D1 | Cloisonnement entre membres | `owner` est un champ texte libre, aucun endpoint ne filtre : chacun voit et modifie tout | |
| D2 | Réglages globaux | `settings.json` est partagé, un membre change les défauts de tous | |
| D3 | Générations concurrentes | Verrou par niche seulement : N membres peuvent saturer la tour | |
| D4 | Liste de liens partagée | `links.txt` / `clip_links.txt` : dernier écrivain gagne | |
| D5 | Suppression d'assets partagés | N'importe qui peut effacer un fichier utilisé par la niche d'un autre | |

---

## Anomalies

Une ligne par écart constaté. Niveaux : 🔴 bloquant (empêche d'ouvrir à l'équipe) —
🟠 gênant (contournable mais coûteux) — 🟡 finition.

| # | Bloc | Niveau | Description | État |
|---|---|---|---|---|
| A1 | R2.1 | 🟠 | « Uploader un fichier » n'ouvrait pas l'explorateur : deux contrôles côte à côte, le retour d'erreur à l'opposé de l'écran | ✅ corrigé — `fix/upload-un-seul-bouton` |
