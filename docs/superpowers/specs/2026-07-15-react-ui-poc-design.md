# PoC React/shadcn — onglet Catalogue

**Date :** 2026-07-15
**Branche :** `feat/react-ui-poc`
**Statut :** design approuvé

## Objectif

Évaluer le confort de mise en forme et l'UX d'une stack front moderne (React +
Tailwind + shadcn/ui) sur un onglet représentatif de l'usine à vidéos, **sans
modifier le master ni le backend Flask**. On porte uniquement l'onglet
**Catalogue** (upload de sons/clips, liens YouTube, tables, suppression) — celui
qui vient d'être retravaillé et qui concentre le plus de motifs d'UI.

Décision : si le PoC est concluant, on portera le reste de l'interface plus tard.
Sinon, la branche reste un simple test jetable.

## Contraintes

- Le backend Flask (`webui.py`) et son template (`templates/index.html`)
  **ne changent pas**. Le master reste intact ; tout vit dans `frontend/`.
- Aucun impact sur les tests Python existants (Flask inchangé → 118 tests verts).
- On introduit Node/npm (déjà présents : Node 24, npm 11).

## Architecture & intégration

- Nouveau dossier `frontend/` à la racine, isolé : app **Vite + React +
  TypeScript + Tailwind + shadcn/ui**.
- Le front consomme les **APIs JSON existantes** de Flask : `/api/login`,
  `/api/state`, `/api/tracks`, `/api/clips`, `/api/links`, `/api/clip-links`,
  `/api/download`, `/api/clips/download`, `/api/jobs/<id>`, et les nouveaux
  `DELETE /api/tracks/<name>` / `DELETE /api/clips/<name>`.
- **Mode dev = proxy Vite** (décision retenue, la moins invasive) :
  - `npm run dev` lance Vite sur le port **5173**.
  - `vite.config.ts` proxifie tout `/api` vers `http://127.0.0.1:8765`.
  - On teste sur `http://localhost:5173`. Flask tourne en parallèle comme
    d'habitude (`uv run python webui.py`).
  - Le cookie de session (login) transite par le proxy → login fonctionnel.
- **Écarté pour le PoC** : builder dans `static/` + route Flask `/react`. Plus
  lourd, non nécessaire pour juger l'ergonomie. À faire seulement si le PoC est
  validé et qu'on veut une intégration servie par Flask.
- `.gitignore` : ajouter `frontend/node_modules/` et `frontend/dist/`.

## Périmètre fonctionnel (parité avec l'onglet actuel)

Mini-shell SPA :

1. **Gate de login** — formulaire (nom + mot de passe) → `POST /api/login`.
   Si `/api/state` renvoie 401, on affiche le login. Après login, on charge
   l'état.
2. **Onglet Catalogue**, deux sections via un composant `Tabs` :
   - **Sons** (`tracks/`) :
     - Upload de fichier → `POST /api/tracks` (multipart).
     - Ajout de lien YouTube : champ + liste des liens (persistés via
       `POST /api/links`), bouton « Télécharger les sons » → `POST /api/download`
       puis **suivi de job** par polling `GET /api/jobs/<id>` jusqu'à
       `done`/`failed`.
     - Table des morceaux (`state.tracks`) avec taille + bouton **supprimer**
       (modale de confirmation → `DELETE /api/tracks/<name>`).
   - **Clips** (`clips/`) : strictement le même schéma avec `/api/clips`,
     `/api/clip-links`, `/api/clips/download`, `DELETE /api/clips/<name>`.

## Composants & structure front

- Composants shadcn : `Tabs`, `Card`, `Button`, `Input`, `Table`, `Dialog`
  (confirmation de suppression), `Sonner` (toasts).
- Data-fetching : simple `fetch` encapsulé dans un petit client `api.ts`
  (gère JSON, erreurs, 401) + hooks `useState`/`useEffect`. **Pas de React Query**
  pour un PoC.
- Découpage :
  - `src/lib/api.ts` — client fetch + types de l'état.
  - `src/components/ui/*` — composants shadcn générés.
  - `src/features/catalogue/` — `Catalogue.tsx`, `AssetSection.tsx` (générique
    Sons/Clips paramétré par endpoints), `LinkManager.tsx`, `JobLog.tsx`.
  - `src/App.tsx` — shell + gate login + onglet.
  - `src/main.tsx`, `index.css` (Tailwind + variables de thème).

`AssetSection` est paramétré (uploadUrl, deleteUrl, linksField, downloadUrl,
listKey…) pour que Sons et Clips partagent le même composant — un seul endroit à
comprendre et tester.

## Identité visuelle

Thème Tailwind/shadcn calé sur Dancing Dead :

- Fond noir OLED (`--background` ≈ `#0a0a0a`), texte clair.
- `--primary` = rouge accent `#ff1e46`.
- Typo **Fira Sans** (UI) / **Fira Code** (mono), chargées via Google Fonts
  comme aujourd'hui.
- Dark mode par défaut (classe `dark` sur `<html>`).

Objectif : que le PoC ressemble à l'app actuelle, en mieux structuré.

## Tests & vérification

- Vérification **manuelle** : lancer Flask + `npm run dev`, se connecter, puis
  exercer chaque flux (upload fichier, ajout/suppression de lien, téléchargement
  avec suivi de job, suppression d'un asset avec confirmation).
- Pas d'infra de test JS pour un PoC (Vitest éventuellement plus tard).
- Les tests Python restent la garantie du backend — inchangés.

## Hors périmètre

- Les onglets Niches, Presets, Réglages.
- Le détail niche, la génération de vidéos, la bibliothèque.
- Le build servi par Flask (route `/react`).
- Toute modification de `webui.py`, `db.py`, `templates/index.html`.
