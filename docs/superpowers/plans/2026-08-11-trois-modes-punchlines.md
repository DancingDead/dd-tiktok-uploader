# Trois modes de texte incrusté — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter un troisième mode de texte incrusté, `llm_unique` — UNE punchline générée, stable du début à la fin de la vidéo — pour qu'un lot de N variantes donne N punchlines différentes, chacune fixe.

**Architecture :** `apply_subtitles` gagne une branche partagée avec le mode `fixe` (une caption unique sur tout l'EDL, sans créneaux) ; seule la provenance du texte diffère — saisi à la main pour `fixe`, `generate_punchlines(..., count=1, seed)` pour `llm_unique`. Le serveur autorise la nouvelle valeur, l'UI passe de deux à trois boutons radio. Aucun changement de schéma en base, aucune migration.

**Tech Stack :** Python 3.12 + pytest (uv), Flask, React + TypeScript (Vite).

**Spec :** `docs/superpowers/specs/2026-08-11-trois-modes-punchlines-design.md`

## Global Constraints

- Commandes : `uv run pytest`, jamais `pip`. Front : `cd frontend && npm run build`.
- Valeurs de `subtitles.mode` : `"llm"` (défaut), `"llm_unique"`, `"fixe"`. Exactement ces chaînes, en base comme dans l'UI.
- Un mode inconnu doit continuer de **dégrader vers `llm`** dans `apply_subtitles` (test existant `test_unknown_mode_degrades_to_llm`) — mais être **refusé en 400** par `coerce_subtitles`. Ces deux comportements coexistent volontairement : le moteur ne bloque jamais un rendu, l'API refuse une saisie invalide.
- L'usine ne bloque jamais sur le LLM : un échec rend `[]` puis une caption vide, jamais une exception.
- Les réglages `x`, `y`, `size` restent **communs aux trois modes**. Ne pas ajouter de champ par mode.
- Commentaires et libellés en français, comme le reste du dépôt.

---

### Task 1 : le mode `llm_unique` dans le moteur

**Files:**
- Modify: `beatsync.py:1551-1571` (`apply_subtitles`)
- Test: `tests/test_subtitles.py` (ajouter une section après le bloc « Mode texte fixe », vers la ligne 326)

**Interfaces:**
- Consomme : `generate_punchlines(preprompt, count, seed, cache_dir=None, model=...) -> list[str]` (existant, inchangé) et `assign_caption_slots(edl, min_dur) -> int` (existant, inchangé).
- Produit : `apply_subtitles(edl, config, seed, cache_dir=None) -> list[dict]` — signature inchangée. Nouveau comportement quand `config["subtitles"]["mode"] == "llm_unique"` : chaque entrée de l'EDL reçoit la même valeur `caption`.

- [ ] **Step 1 : écrire les tests en échec**

Ajouter dans `tests/test_subtitles.py`, après `test_fixed_mode_disabled_leaves_the_edl_alone` et avant `test_unknown_mode_degrades_to_llm` :

```python
# --- Mode « une punchline générée » -----------------------------------------


def unique_config(**subs):
    return {**DEFAULT, "subtitles": {**DEFAULT["subtitles"], "enabled": True,
                                     "mode": "llm_unique",
                                     "preprompt": "motivation gym", **subs}}


def test_unique_mode_puts_the_same_generated_caption_everywhere(monkeypatch):
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model: ["ON LACHE RIEN"])
    edl = make_edl([0.0, 0.4, 0.8, 1.2, 1.6])
    out = apply_subtitles(edl, unique_config(), seed=1)
    assert [e["caption"] for e in out] == ["ON LACHE RIEN"] * 5


def test_unique_mode_asks_the_llm_for_exactly_one_punchline(monkeypatch):
    """Une seule punchline demandée, un seul appel — et surtout pas un appel
    par créneau : c'est ce qui distingue ce mode du mode « llm »."""
    calls = []
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model: (calls.append(n) or ["X"]))
    edl = make_edl([0.0, 0.4, 1.8, 3.4, 5.0])
    apply_subtitles(edl, unique_config(), seed=1)
    assert calls == [1]


def test_unique_mode_gives_a_different_punchline_per_seed(monkeypatch):
    """La promesse du mode : un lot de N variantes donne N punchlines. Chaque
    variante a sa seed, et la seed atteint le prompt."""
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model: [f"PUNCH {seed}"])
    edl_a = make_edl([0.0, 0.4, 0.8])
    edl_b = make_edl([0.0, 0.4, 0.8])
    a = apply_subtitles(edl_a, unique_config(), seed=11)[0]["caption"]
    b = apply_subtitles(edl_b, unique_config(), seed=22)[0]["caption"]
    assert a != b


def test_unique_mode_degrades_to_no_text_when_the_llm_fails(monkeypatch):
    """L'usine ne bloque jamais sur le LLM : la vidéo sort sans texte."""
    def boom(pp, n, seed, model):
        raise RuntimeError("LM Studio éteint")

    monkeypatch.setattr(beatsync, "_call_llm", boom)
    edl = make_edl([0.0, 0.4, 0.8])
    out = apply_subtitles(edl, unique_config(), seed=1)
    assert [e["caption"] for e in out] == ["", "", ""]


def test_unique_mode_does_not_build_caption_slots(monkeypatch):
    """Pas de créneaux dans ce mode : le texte ne change jamais, `min_dur` n'a
    rien à découper."""
    monkeypatch.setattr(beatsync, "_call_llm", lambda pp, n, seed, model: ["X"])
    monkeypatch.setattr(beatsync, "assign_caption_slots",
                        lambda edl, min_dur: pytest.fail("créneaux calculés à tort"))
    edl = make_edl([0.0, 0.4, 0.8])
    out = apply_subtitles(edl, unique_config(), seed=1)
    assert all("caption_slot" not in e for e in out)


def test_unique_mode_disabled_leaves_the_edl_alone():
    edl = make_edl([0.0, 0.4])
    out = apply_subtitles(edl, unique_config(enabled=False), seed=1)
    assert all("caption" not in e for e in out)
```

- [ ] **Step 2 : lancer les tests pour les voir échouer**

Run: `uv run pytest tests/test_subtitles.py -k unique -v`

Expected: ÉCHEC. `test_unique_mode_puts_the_same_generated_caption_everywhere` échoue parce que le mode inconnu retombe aujourd'hui sur le chemin `llm`, qui répartit les punchlines par créneau au lieu de poser la même partout ; `test_unique_mode_does_not_build_caption_slots` échoue sur l'appel à `assign_caption_slots`.

- [ ] **Step 3 : écrire l'implémentation minimale**

Dans `beatsync.py`, remplacer le début de `apply_subtitles` (la branche `if sub.get("mode") == "fixe":`) par :

```python
    mode = sub.get("mode")
    if mode in ("fixe", "llm_unique"):
        # Une caption unique, du début à la fin : ni créneaux, ni `min_dur`, le
        # texte ne changeant jamais. Les deux modes ne diffèrent que par la
        # PROVENANCE du texte, d'où le chemin commun.
        if mode == "fixe":
            text = sub.get("text", "")
        else:
            # count=1 : un seul appel, et une clé de cache distincte de celle du
            # mode « llm » (le count entre dans la clé). La seed, elle, varie
            # d'une variante à l'autre — c'est ce qui donne N punchlines pour un
            # lot de N vidéos, sans code dédié.
            lines = generate_punchlines(sub.get("preprompt", ""), 1, seed, cache_dir,
                                        sub.get("model", "claude-opus-4-8"))
            text = lines[0] if lines else ""
        for entry in edl:
            entry["caption"] = text
        return edl
```

Le reste de la fonction (créneaux + `generate_punchlines` par créneau) est inchangé et reste le chemin par défaut, y compris pour un mode inconnu.

- [ ] **Step 4 : lancer les tests pour les voir passer**

Run: `uv run pytest tests/test_subtitles.py -v`

Expected: PASS, y compris les tests existants `test_fixed_mode_*` et `test_unknown_mode_degrades_to_llm`.

- [ ] **Step 5 : vérifier que les nouveaux tests sont discriminants**

Remplacer temporairement `("fixe", "llm_unique")` par `("fixe",)` dans `apply_subtitles`, relancer `uv run pytest tests/test_subtitles.py -k unique`, vérifier que les tests **échouent**, puis rétablir.

Expected: au moins `test_unique_mode_puts_the_same_generated_caption_everywhere` et `test_unique_mode_does_not_build_caption_slots` en échec. Un test qui reste vert ici ne teste rien — le corriger avant de continuer.

- [ ] **Step 6 : lancer la suite complète**

Run: `uv run pytest -q`

Expected: tous les tests passent (647 avant cette tâche, 653 après).

- [ ] **Step 7 : commit**

```bash
git add beatsync.py tests/test_subtitles.py
git commit -m "feat(punchlines): mode llm_unique, une punchline generee sur toute la video"
```

---

### Task 2 : autoriser le mode côté serveur

**Files:**
- Modify: `webui.py:157` (`ALLOWED_SUBTITLE_MODES`)
- Test: `tests/test_webui_platform.py` (à côté de `test_coerce_subtitles_rejects_unknown_mode`, vers la ligne 385)

**Interfaces:**
- Consomme : `coerce_subtitles(subtitles: dict) -> dict` (existant), qui lève `ValueError` sur un mode inconnu.
- Produit : rien de nouveau. `ALLOWED_SUBTITLE_MODES` vaut désormais `{"llm", "llm_unique", "fixe"}`.

- [ ] **Step 1 : écrire le test en échec**

Ajouter dans `tests/test_webui_platform.py`, après `test_coerce_subtitles_rejects_unknown_mode` :

```python
def test_coerce_subtitles_accepts_the_single_punchline_mode():
    """Le mode « une punchline générée » doit traverser la validation, sinon la
    niche ne peut pas être enregistrée depuis l'UI."""
    out = coerce_subtitles({"mode": "llm_unique", "preprompt": "gym", "size": "72"})
    assert out["mode"] == "llm_unique"
    assert out["size"] == 72
```

- [ ] **Step 2 : lancer le test pour le voir échouer**

Run: `uv run pytest tests/test_webui_platform.py::test_coerce_subtitles_accepts_the_single_punchline_mode -v`

Expected: ÉCHEC avec `ValueError: mode de sous-titres inconnu : 'llm_unique'`.

- [ ] **Step 3 : écrire l'implémentation minimale**

Dans `webui.py`, ligne 157 :

```python
ALLOWED_SUBTITLE_MODES = {"llm", "llm_unique", "fixe"}
```

- [ ] **Step 4 : lancer les tests pour les voir passer**

Run: `uv run pytest tests/test_webui_platform.py -v`

Expected: PASS, y compris `test_coerce_subtitles_rejects_unknown_mode` qui doit continuer de refuser une valeur fantaisiste.

- [ ] **Step 5 : commit**

```bash
git add webui.py tests/test_webui_platform.py
git commit -m "feat(punchlines): autorise le mode llm_unique cote serveur"
```

---

### Task 3 : les trois boutons radio dans l'UI

**Files:**
- Modify: `frontend/src/lib/api.ts:10` (type `Subtitles.mode`)
- Modify: `frontend/src/features/niches/NicheDetail.tsx:31` (état `subsMode`), `:216-228` (les radios), `:229-255` (les zones de saisie)

**Interfaces:**
- Consomme : `PATCH /api/niches/<id>` avec `subtitles.mode` valant `"llm" | "llm_unique" | "fixe"` (autorisé par la Task 2 — cette tâche échouera en 400 si la Task 2 n'est pas faite).
- Produit : rien pour les autres tâches.

- [ ] **Step 1 : élargir le type**

Dans `frontend/src/lib/api.ts`, ligne 10 :

```ts
  mode?: "llm" | "llm_unique" | "fixe"
```

- [ ] **Step 2 : élargir l'état local**

Dans `frontend/src/features/niches/NicheDetail.tsx`, ligne 31 :

```tsx
  const [subsMode, setSubsMode] = useState<"llm" | "llm_unique" | "fixe">(
    niche.subtitles?.mode ?? "llm",
  )
```

- [ ] **Step 3 : passer à trois radios**

Remplacer le bloc des radios (le `<div className="flex gap-4 text-sm">` et son contenu) par :

```tsx
                {/* Trois modes exclusifs. « Une punchline générée » est le mode
                    qui sert à trier un lot : chaque variante porte UN texte,
                    différent d'une variante à l'autre. */}
                <div className="flex flex-col gap-2 text-sm">
                  {([
                    ["llm", "Punchlines générées", "Le texte change à chaque coupe."],
                    ["llm_unique", "Une punchline générée",
                     "Un seul texte généré, du début à la fin. Chaque variante du lot en a un différent."],
                    ["fixe", "Texte fixe", "Le texte que tu écris, du début à la fin."],
                  ] as const).map(([value, label, hint]) => (
                    <label key={value} className="flex items-start gap-2">
                      <input
                        type="radio"
                        name="subs-mode"
                        className="mt-1"
                        checked={subsMode === value}
                        onChange={() => setSubsMode(value)}
                      />
                      <span>
                        {label}
                        <span className="block text-xs text-muted-foreground">{hint}</span>
                      </span>
                    </label>
                  ))}
                </div>
```

- [ ] **Step 4 : afficher la bonne zone de saisie**

Remplacer la condition `{subsMode === "llm" ? (` par `{subsMode !== "fixe" ? (` — la consigne de style vaut pour les **deux** modes générés. Les deux branches (Textarea `preprompt` et Textarea `fixed-text`) sont inchangées par ailleurs.

- [ ] **Step 5 : vérifier la compilation et le lint**

Run: `cd frontend && npm run build && npm run lint`

Expected: build réussi (aucune erreur TypeScript — c'est ce qui prouve que le type élargi et l'état concordent), lint sans erreur.

- [ ] **Step 6 : commit**

```bash
git add frontend/src/lib/api.ts frontend/src/features/niches/NicheDetail.tsx
git commit -m "feat(ui): trois modes de texte incruste dans la niche"
```

---

### Task 4 : vérification bout-en-bout sur la tour

Cette tâche ne produit pas de code. Elle existe parce que le dispositif a un angle mort connu : la vérification se fait sur macOS, le déploiement tourne sur Windows.

**Files:** aucun.

**Interfaces:** aucune.

- [ ] **Step 1 : déployer**

```bash
git push origin master
ssh tour "cd 'C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader'; powershell -ExecutionPolicy Bypass -File deploy\update.ps1"
```

Vérifier ensuite que la tour est bien sur le bon commit — `update.ps1` n'échoue pas si `git pull` est refusé :

```bash
ssh tour "cd 'C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader'; git log --oneline -1"
```

Expected: le commit de la Task 3. Si `links.txt` bloque le pull, l'écarter (`git checkout -- links.txt`) et relancer.

- [ ] **Step 2 : lancer la suite sur la tour**

Run: `ssh tour "cd 'C:\Users\Dancing Dead\Desktop\DEV\dd-tiktok-uploader'; uv run pytest -q"`

Expected: 654 tests passent, zéro échec (647 avant ce plan, +6 en Task 1, +1 en Task 2).

- [ ] **Step 3 : générer un lot réel et regarder les punchlines**

Depuis l'UI (`http://100.74.173.64:8765`) : ouvrir une niche, cocher « Incruster du texte », choisir « Une punchline générée », écrire une consigne de style, enregistrer, puis générer un lot de 3.

Expected : trois vidéos dans la bibliothèque, chacune affichant **une** punchline sous sa vignette, et **trois textes différents**. C'est la promesse du mode ; si les trois punchlines sont identiques, le modèle local ignore la seed et il faut le signaler plutôt que de considérer la tâche finie.

- [ ] **Step 4 : vérifier à l'image**

Ouvrir une des vidéos et confirmer que le texte reste le même du début à la fin, à la position et à la taille réglées.

---

## Notes pour l'implémenteur

- **Ne pas toucher** à `assign_caption_slots`, `generate_punchlines`, `_caption_filter` ni au rendu : la spec est explicite, tout se joue dans la branche de `apply_subtitles`.
- Le test existant `test_unknown_mode_degrades_to_llm` doit rester vert. Si vous le cassez, c'est que la nouvelle branche attrape trop large (par exemple `if mode != "llm"`).
- `generate_punchlines` rend `[]` quand le préprompt est vide : en `llm_unique` sans consigne de style, la vidéo sort sans texte. C'est le comportement attendu, pas un bug à corriger.
