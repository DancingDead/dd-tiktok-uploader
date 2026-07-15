# Front PoC — React/shadcn (onglet Catalogue)

Preuve de concept d'une refonte de l'interface avec **Vite + React + TypeScript +
Tailwind + shadcn/ui**, isolée du reste du projet. Elle ne réécrit que l'onglet
**Catalogue** et consomme les APIs JSON de Flask (`webui.py`) via un proxy Vite.

Le backend Flask n'est pas modifié.

## Lancer en dev

Deux process en parallèle :

```bash
# 1) le backend Flask (à la racine du projet)
uv run python webui.py            # http://127.0.0.1:8765

# 2) le front (dans frontend/)
cd frontend
npm install                       # première fois seulement
npm run dev                       # http://localhost:5173
```

On ouvre **http://localhost:5173** : tout `/api/*` est proxifié vers le Flask
local (le cookie de session passe par le proxy, donc le login fonctionne).

Pour cibler un Flask sur un autre port :

```bash
VITE_API_TARGET=http://127.0.0.1:8790 npm run dev
```

## Périmètre

- Login + onglet Catalogue (sections **Sons** et **Clips**).
- Chaque section : upload de fichier, ajout/retrait de liens YouTube,
  téléchargement avec suivi de job, table des assets avec suppression confirmée.
- Hors périmètre : Niches, Presets, Réglages (voir la spec du design).

## Structure

- `src/lib/api.ts` — client fetch + types de l'état.
- `src/features/catalogue/` — `Catalogue`, `AssetSection` (générique Sons/Clips),
  `JobLog`, `ConfirmDialog`.
- `src/components/ui/` — composants shadcn/ui (éditables).
- `src/index.css` — thème Dancing Dead (noir OLED, accent `#ff1e46`, Fira).
