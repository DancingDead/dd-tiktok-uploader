# 07 — Index des fonctions

> Les **53 fonctions de module** + **6 fonctions imbriquées** de `beatsync.py`.
> Une ligne chacune, dans l'ordre du fichier.
> ← [vue d'ensemble](00-beatsync-vue-ensemble.md)

**Légende** — `★` = point d'entrée public · `_` = privée (convention) ·
**pure** = aucun I/O ni RNG, testable directement

---

## Configuration (l.86-242)

| Ligne | Fonction | Rôle | |
|---|---|---|---|
| 86 | `merge_settings(base, overrides)` | Applique des réglages sur une config **sans muter la base**. Les dicts imbriqués (`effects`, `accents`) fusionnent clé par clé ; les clés inconnues sont ignorées. C'est ce qui fait `settings.json ← preset`. | pure |
| 107 | `apply_format(config)` | Pose `width`/`height` d'après `format`. Format inconnu → vertical. Appelée en tête de `generate_video` : **le point de passage unique** du CLI et de l'usine. | pure |
| 116 | `_clamp_speed(value)` | Borne toute vitesse à [0.5, 1.5]. Défensif : l'UI n'impose pas de borne. | pure |
| 188 | `blackout_boundaries(boundaries, drop_out, beat_dur, config, fps)` ★ | Réécrit la grille du build-up en alternance éclair / noir, **comptée à rebours depuis le drop** — c'est ce qui garantit que le segment s'y terminant est une image. Rend aussi les frames de début des noirs. | pure |
| 236 | `load_settings(path=None)` | `DEFAULT_CONFIG` fusionné avec `settings.json`. Le point de départ de toute config. | I/O |

Détail → [03 cadrage](03-cadrage.md) pour `apply_format`.

---

## Vitesse et impacts (l.121-233)

| Ligne | Fonction | Rôle | |
|---|---|---|---|
| 121 | `is_impact(beat_index, anchor, impact_beats)` ★ | Ce beat porte-t-il le motif de vitesse ? Vrai pour l'ancre et tous les multiples d'`impact_beats`, **avant comme après**. Index négatif = borne de fenêtre, jamais un impact. | pure |
| 130 | `_ramp_decision(start_beat, end_beat, duration, anchor, config)` | Retourne `(vitesse, ramp_slow)`. Le booléen distingue un ralenti **voulu par la ramp** d'un ralenti venant de `clip_speed` — il décide seul du flux optique au rendu. | pure |
| 151 | `ramp_speed(...)` ★ | Façade publique de `_ramp_decision` : ne rend que la vitesse. Ralenti si le segment **finit** sur un impact, accéléré s'il **commence** dessus, ralenti prioritaire si les deux. | pure |
| 160 | `merge_boundaries_before_impacts(cut_beats, anchor, config)` ★ | Retire les coupes des `slow_beats−1` beats avant chaque impact → les segments fusionnent, le ralenti a le temps de se voir. `kept or cut_beats` : une fenêtre sans impact garde sa grille. | pure |
| 175 | ↳ `distance_to_next_impact(beat_index)` | Imbriquée. Modulo vers l'impact suivant ; 0 si le beat **est** un impact. | pure |

Détail → [04 build_edl](04-build-edl.md).

---

## Analyse audio (l.245-702)

| Ligne | Fonction | Rôle | |
|---|---|---|---|
| 245 | `analyze_audio(track_path)` ★ | librosa : beats (en s), BPM, enveloppe RMS, durée. Import de librosa **dans** la fonction (~2 s, inutile aux tests purs). | I/O |
| 631 | `find_drop(analysis, config)` ★ | Instant qui maximise le contraste d'énergie ±8 s, calé sur un beat. **`None` si l'énergie est plate** — le montage se fait alors sans drop. | pure |
| 663 | `find_calm(analysis, config, duration)` ★ | Miroir : la fenêtre à énergie moyenne minimale, en excluant celles qui contiennent du silence (sinon on choisirait l'intro muette). | pure |

Détail → [01 analyse audio](01-analyse-audio.md).

---

## Scan des clips (l.261-629)

| Ligne | Fonction | Rôle | |
|---|---|---|---|
| 261 | `load_clips(folder)` ★ | Métadonnées ffprobe (w, h, durée, ratio, kind). **`sorted()`** = garde-fou de reproductibilité n°1. `duration: None` pour les images. | I/O |
| 303 | `classify_frames(frames, sample_dt)` ★ | Par frame : orange (carte Crunchyroll), noir (générique), mouvement. Cast `int16` obligatoire — un `diff` sur `uint8` wrappe. | pure |
| 332 | `_edge_runs(profile)` | Longueurs des segments **sombres continus** en tête et en queue d'un profil. Une ligne sombre isolée au milieu n'est pas une bande. | pure |
| 341 | `content_rect(frames)` ★ | Rectangle utile d'un clip (bandes noires retirées), **en fractions** du cadre, ou `None`. 95e percentile sur les frames — pas le maximum, qu'un sous-titre incrusté dans la bande ferait échouer. | pure |
| 371 | `usable_intervals(classification, duration, ...)` ★ | Runs consécutifs de frames valides → plages temporelles. **Deux seuils de mouvement** : bas par échantillon (garde les micro-pauses des combats), haut sur la moyenne (écarte les pans d'établissement). | pure |
| 418 | `interval_dual_ratio(clip, interval)` ★ | Fraction d'échantillons « duel » sur une plage. Découpe le tableau `dual` sur `scan_dt`. `0.0` si le clip n'est pas scanné. | pure |
| 432 | `find_final_scene(clips, min_source)` ★ | La plage la plus badass du **dernier tiers** des clips (le climax). Score = duel 1.0 + présence 0.6 + mouvement normalisé 0.6. `None` si rien d'exploitable. Départage déterministe. | pure |
| 474 | ↳ `score(clip, interval, dual, motion)` | Imbriquée. Applique `FINAL_SCENE_WEIGHTS`. | pure |
| 496 | `_char_presence(frames)` | Par frame : présence (visage 1.0 / contours 0.6 / rien 0.0), centre d'intérêt horizontal, duel. Masque le logo **avant** détection ; contours cherchés dans la bande centrale 40 % seulement. | I/O (cv2) |
| 546 | `_scan_one(clip)` | Décodage FFmpeg 640×360 @ 2 fps → classification + présence → **mute le dict** du clip. Deux résolutions : 640×360 pour la détection, 32×18 pour couleur/mouvement. | I/O |
| 577 | `_scan_payload(clip)` | Sérialise le résultat de scan en JSON (numpy → listes Python). | pure |
| 588 | `_apply_scan_payload(clip, payload)` | L'inverse : réhydrate un cache en numpy. | pure |
| 599 | `scan_clips(clips, cache_dir=None)` ★ | Boucle sur les clips avec cache par fichier (clé md5 du chemin, invalidé par mtime). **Cache corrompu = miss**, jamais une erreur. Images sautées. | I/O |

Détail → [02 scan des clips](02-scan-clips.md).

---

## Cadrage (l.704-785)

| Ligne | Fonction | Rôle | |
|---|---|---|---|
| 704 | `snap_end_to_phrase(end, drop_time, beats, ...)` ★ | Étend la fin au multiple de 16 beats après le drop — la musique ne coupe pas en pleine phrase. `np.median` (pas `mean`) : un beat raté fausserait la moyenne. Inchangé sans drop. | pure |
| 720 | `resolve_window(analysis, config, start, duration)` ★ | Pose `drop_time`, `start`, `end` dans config. **Le seul de ce bloc qui mute** — c'est le point où la fenêtre est fixée. `--start` gagne toujours sur l'auto. | pure* |
| 751 | `frame_extract(clip, clip_in, source_needed, config)` ★ | `(focus_x, layout)` pour un extrait. Minimum 3 échantillons (un segment d'un beat n'en couvre parfois qu'un). Les deux layouts de secours dépendent du **format de sortie**. | pure |

\* mute `config` volontairement.
Détail → [03 cadrage](03-cadrage.md).

---

## build_edl (l.787-1250)

| Ligne | Fonction | Rôle | |
|---|---|---|---|
| 787 | `free_windows(intervals, consumed, source_needed, margin=0.5)` ★ | Portions d'un clip **pas encore montrées**. Soustrait les plages consommées, élargies de `margin` de chaque côté — sans quoi deux extraits qui se touchent restent visuellement identiques. C'est l'anti-répétition. | pure |
| 815 | `build_edl(analysis, clips, config, seed)` ★★ | **Le cœur.** ~300 lignes, 7 étapes : percentiles → drop → marche beat par beat → fusion → quantification → scène de fin → attribution. `random.Random(seed)` local = garde-fou n°2. | pure |
| 853 | ↳ `step_at(i)` | Beats jusqu'à la prochaine coupe. Ordre = hiérarchie : strobo (1) > mode fixe > énergie (4/2/1). | pure |
| 861 | ↳ `tier_at(i)` | `calm` \| `mid` \| `intense` d'après le percentile. Pilote les effets. | pure |
| 948 | ↳ `intervals_of(clip)` | Plages d'un clip. **Clé absente ≠ `[]`** : absente = pas scanné (clip entier utilisable), `[]` = scanné et inutilisable (clip exclu). | pure |

Détail → [04 build_edl](04-build-edl.md).

---

## Punchlines (l.1252-1491)

| Ligne | Fonction | Rôle | |
|---|---|---|---|
| 1252 | `_caption_font()` | Première police **système** disponible (macOS puis Linux). Dernier repli. | I/O |
| 1271 | `resolve_caption_font(name)` ★ | Nom logique → fichier embarqué OFL. Deux replis : nom inconnu → `impact` ; fichier absent → police système. | I/O |
| 1280 | `_drawtext_escape(text)` | Échappe `: ' % , ; [ ]` pour le filtergraph. **L'ordre compte** : antislashs d'abord, retours à la ligne en dernier. | pure |
| 1292 | `_drawtext_fontfile(path)` | Chemin de police pour FFmpeg. Sous Windows, le `:` du lecteur veut **deux** antislashs (double unescape). No-op sur POSIX. | pure |
| 1301 | `assign_caption_slots(edl, min_dur)` ★ | Regroupe les segments en créneaux ≥ `min_dur` : le texte change **à une coupe**, jamais en plein plan. Retourne le nombre de créneaux = combien de punchlines demander. | pure |
| 1315 | `_load_dotenv(path=None)` | Charge `.env` dans `os.environ` **sans écraser** (`setdefault`) : une variable du shell reste prioritaire. | I/O |
| 1336 | `_punchline_user_prompt(preprompt, count, seed)` | Construit le prompt utilisateur. La seed y figure comme « Variation n° » — même pour un modèle sans paramètre `seed`. | pure |
| 1341 | `_llm_backend()` | Lit `LLM_BACKEND`, défaut `lmstudio` (local, coût nul). | I/O |
| 1346 | `_call_anthropic(preprompt, count, seed, model)` | SDK Anthropic, `output_config.format` + JSON Schema. Requiert `ANTHROPIC_API_KEY`. | réseau |
| 1369 | `_call_lmstudio(preprompt, count, seed, model)` | POST HTTP stdlib vers un serveur compatible OpenAI. `seed` transmis. LM Studio ≥ 0.4 exige `json_schema`. | réseau |
| 1418 | `_call_llm(preprompt, count, seed, model)` | Dispatche sur le backend, tente `LLM_FALLBACK` en repli. Résout par **`globals()[nom]`** pour rester monkeypatchable. | réseau |
| 1442 | `generate_punchlines(...)` ★ | Cache par (backend, modèle, préprompt, count, seed). **`except Exception` → `[]`** : l'usine ne bloque jamais sur le LLM. | I/O |
| 1470 | `apply_subtitles(edl, config, seed, cache_dir)` ★ | Pose `entry["caption"]`. Trois chemins : désactivé (rien), mode `fixe` (même texte partout, ni LLM ni cache), mode `llm` (créneaux + génération). | I/O |

Détail → [05 punchlines](05-punchlines.md).

---

## Rendu (l.1524-1807)

| Ligne | Fonction | Rôle | |
|---|---|---|---|
| 1524 | `_run_ffmpeg(args)` | Lance ffmpeg, lève avec **la commande complète + stderr** en cas d'échec — copiable-collable pour rejouer le cas. | I/O |
| 1531 | `color_grade_filter(grade)` ★ | Fragment `eq=` pour chaud / froid / delave. `""` si neutre ou inconnu → le filtre n'est pas ajouté. | pure |
| 1540 | `grain_filter(amount)` ★ | Fragment `noise=`, plus une dérive chroma au-delà de 0,6 (look VHS). Clampe [0, 1]. | pure |
| 1553 | `glitch_amount(accents)` ★ | Lit un réglage qui était booléen et est devenu 0–1 : `True`→0.6, nombre→clampé. **Compat sans migration de base.** | pure |
| 1565 | `_segment_input_args(entry)` | Args d'entrée. `-ss` **avant** `-i` (seek rapide) + rab 0,5 s. Rab **supprimé** si `freeze > 0` : étiré par le ralenti, il annulerait le figé. Images : `-loop 1`. | pure |
| 1594 | `kenburns_filter(entry, config)` ★ | Zoom + pan lents sur une image fixe. Les **sens sont tirés à la seed dans `build_edl`** — le filtre n'est que déterministe. | pure |
| 1607 | `_caption_filter(entry, config)` | Fragment `drawtext` d'un segment, ou None. Partagé par les segments ordinaires et les écrans noirs, qui gardent leur texte. | pure |
| 1637 | `_segment_filters(entry, config)` | La grosse fonction : construit toute la chaîne. Ordre = raisonnement (delogo avant crop, minterpolate après layout, drawtext après les accents). Rend `-vf` ou `-filter_complex`. | pure |
| 1621 | ↳ `_coerce(value, cast, default)` | Imbriquée. Dernière défense avant le rendu : `x`/`y`/`size` invalides retombent sur le défaut au lieu de planter. | pure |
| 1762 | `render(edl, audio_path, output_path, config)` ★ | 1 mp4 par segment dans un tmpdir, puis concat **en copie de flux** + audio. `-frames:v` exact contre la dérive 23,976 fps. `-bitexact` = garde-fou n°3. | I/O |

Détail → [06 rendu](06-rendu.md).

---

## Orchestration (l.1493, 1809)

| Ligne | Fonction | Rôle | |
|---|---|---|---|
| 1493 | `generate_video(track, clips, config, seed, output_path, ...)` ★★ | **Le point d'entrée réutilisable.** Enchaîne analyse → format → fenêtre → EDL → punchlines → rendu. Ne mute pas `config` (copie interne). Retourne `{segments, window, captions}`. Partagé par le CLI et `generate_niche.py`. | I/O |
| 1809 | `main()` | Habillage CLI argparse. Valide les chemins, charge et scanne les clips, applique les surcharges (`--cut-every`, `--section`, `--format`, `--subtitles`), appelle `generate_video`. | I/O |

`log=lambda m: None` par défaut dans `generate_video` : silencieux en test, `print` en CLI, capture vers l'UI en usine.

---

## Les constantes de module

| Ligne | Constante | Valeur | Rôle |
|---|---|---|---|
| 19 | `VIDEO_EXTENSIONS` | mp4, mov, m4v, mkv, webm, avi | extensions vidéo acceptées |
| 21 | `IMAGE_EXTENSIONS` | jpg, jpeg, png, webp | extensions image acceptées |
| 24 | `IMAGE_MAX_DUR` | `0.6` | durée max d'une image au montage. **Constante, pas un réglage** — le catalogue d'images ne s'expose pas |
| 26 | `DEFAULT_CONFIG` | dict | tous les réglages et leurs défauts |
| 104 | `FORMATS` | `{vertical, carre}` | dimensions de sortie |
| 233 | `SETTINGS_PATH` | `./settings.json` | |
| 412 | `FINAL_SCENE_TAIL` | `1/3` | queue du clip où chercher le climax |
| 415 | `FINAL_SCENE_WEIGHTS` | `{dual 1.0, presence 0.6, motion 0.6}` | le duel prime |
| 327 | `BAR_LUMA_MAX` | `16.0` | une bande noire reste sous cette luminance |
| 328 | `BAR_MIN_FRACTION` | `0.015` | en dessous, bruit de bord, pas une bande |
| 329 | `BAR_MAX_TOTAL` | `0.30` | au-delà, scène de nuit → pas de rognage |
| 489 | `SCAN_CACHE_VERSION` | `2` | invalide les caches dont le **schéma** a changé |
| 486 | `SCAN_FPS` | `2.0` | échantillons/s au scan |
| 490 | `SCAN_W, SCAN_H` | `640, 360` | résolution de **détection** |
| 491 | `SMALL_W, SMALL_H` | `32, 18` | résolution **couleur/mouvement** |
| 492 | `CASCADE_PATH` | `assets/lbpcascade_animeface.xml` | |
| 493 | `EDGE_PRESENCE_THRESHOLD` | `0.008` | fraction de « trait d'encre » |
| 1243 | `_CAPTION_FONTS` | 5 chemins | polices système, replis |
| 1259 | `FONTS_DIR` | `assets/fonts` | |
| 1261 | `_FONT_FILES` | 6 entrées | nom logique → fichier OFL embarqué |
| 1330 | `_PUNCHLINE_SYSTEM` | str | consigne partagée par tous les backends |
| 1415 | `_LLM_BACKENDS` | `{anthropic, lmstudio}` | **noms** de fonctions, pas les objets |

---

## Récapitulatif

**48 fonctions de module** + **6 imbriquées** = 54.

Sur les 48 fonctions de module :

| Catégorie | Nombre |
|---|---|
| **Pures** (testables sans I/O) | 29 |
| Avec I/O (fichier, FFmpeg, réseau) | 19 |

Les 6 fonctions imbriquées sont toutes pures — soit **35 fonctions pures sur
54**. Et les 19 impures sont concentrées aux extrémités : lecture des fichiers
d'un côté, FFmpeg et LLM de l'autre. Le milieu du pipeline — décider du montage
— est intégralement pur.

C'est ce qui permet 21 fichiers de tests qui ne lancent **jamais** FFmpeg,
librosa ni le moindre appel réseau.

### Les 4 fonctions à comprendre en premier

1. **`build_edl`** (l.815) — toutes les décisions de montage
2. **`generate_video`** (l.1493) — l'enchaînement complet, 30 lignes
3. **`usable_intervals`** (l.371) — pourquoi tel bout de clip est ou non retenu
4. **`_segment_filters`** (l.1637) — comment une décision devient une image
