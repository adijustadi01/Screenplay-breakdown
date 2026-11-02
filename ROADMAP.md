# Roadmap — full-build memory

A screenplay production breakdown tool. It reads a full feature script (300–400 pages),
extracts everything production needs, and lets an Assistant Director review, correct
and query the results.

**Rules that hold in every stage**

1. **Accuracy and traceability beat cleverness.** Every extracted fact links to the exact
   passage it came from and carries `confidence`, `needs_review` and `review_status`.
2. **Never invent a value to look complete.** Ambiguity becomes a review item: "a huge
   crowd" = unknown headcount; "CONTINUOUS" = inherited time, flagged.
3. **The LLM extracts; code counts.** Every report and cross-count is a deterministic
   database query over reviewed data. Reports are always reproducible and auditable.
4. **Nothing is silently dropped.** Every page and every line of text is either assigned
   to a scene, marked as non-scene (title page, page furniture), or flagged.
5. **Human corrections are sacred.** CONFIRMED/EDITED facts are never overwritten by a
   re-run; conflicts become review items. Every change is in the audit log.
6. **Never force-collapse vocabularies.** DUSK stays DUSK. Grouping is a visible report
   option, never a stored transformation.

---

## Stage 1 — Foundation: scenes, cast, locations, cross-counts  *(complete — awaiting your test on a real script)*

| Step | Module | Status |
|---|---|---|
| 0 | Stack, folder structure, ROADMAP, DATA_MODEL, schema | ✅ done (approved) |
| 1 | Script import (PDF / DOCX / FDX / Fountain, OCR fallback) + coverage check | ✅ |
| 1b | Scene detection: headings, original numbers, page refs, 1/8-page lengths | ✅ |
| 2 | Per-scene extraction (Claude, verified in code; offline rules fallback): characters (present/mentioned, speaking/silent), location + sub-location, synopsis | ✅ |
| 3 | Reconciliation: character aliases, casting variants, location variants/parents → review-queue proposals (undoable) | ✅ |
| 4 | Reports (computed in code): scene, character, location, character-combination, day/night cross-tabs | ✅ |
| 5 | Review queue UI: confirm / edit / reject / accept / undo with source passage; corrections propagate to reports | ✅ |
| 6 | Query panel: composable filters (location + INT/EXT + time + character), every number drills down | ✅ |
| — | Sample script (15 scenes, 6 formats) + ground-truth JSON + accuracy command | ✅ |
| — | Verified end-to-end on a ~300-page script | ✅ synthetic 305-page PDF (4 s) and scan (2 min 40 s OCR); ⏳ needs your real script + live Claude run |

**Stage 1 acceptance:** `pytest tests/test_stage1_acceptance.py -v` (one test per requirement).

**Stage 1 exit criteria:** a real 300-page script imports with a clean coverage report,
extraction accuracy is measured against the sample ground truth, and every report number
drills down to the scenes behind it.

---

## Stage 2 — Departmental breakdowns  *(not started — do not build yet)*

Each department becomes an extracted-fact table using the same `ProvenanceMixin`
(source passage, confidence, needs_review, review_status) and, where counts matter,
`QuantityMixin` (min/max/raw — unknown stays unknown). Each item links to scene(s) and,
where relevant, to a character (whose costume? whose prop?). All new tables plug into
the existing generic review queue (`review_items.entity_table/entity_id`).

| Department | What to extract | Notes |
|---|---|---|
| Vehicles | picture vehicles, who drives, moving/static, process/tow | "INT. CAR - MOVING" implies a vehicle |
| Props | hand props, hero props, consumables, repeats | continuity: same prop across scenes |
| Set dressing | dressing called for in action | distinct from props |
| Costumes | per character per scene, changes, damage/blood continuity | costume change numbering (C1, C2…) |
| Makeup / hair | wounds, ageing, prosthetics, wigs, special MU | ties to casting role (young/old) |
| Background / extras | crowd type, headcount (often unknown → flagged), featured extras | "a huge crowd" = unknown, never guessed |
| Animals / wranglers | species, count, action required | |
| Stunts / action | stunt type, performers, doubles needed | links to `casting_roles` STUNT_DOUBLE |
| VFX / SFX | VFX shots, practical effects (rain, fire, smoke, squibs) | |
| Songs / music / sound | on-screen songs, playback, lip-sync, licensed music, special sound | Indian features: song sequences as their own unit |
| Production requirements | special equipment (crane, drone, underwater), permits, weather, child labour rules, night-shoot flags | |

Stage 2 also adds: department reports, per-department review filters, export to CSV/PDF
breakdown sheets (one sheet per scene, colour-coded by department).

---

## Stage 3 — Revision tracking + scheduling reports  *(not started — do not build yet)*

**Revision tracking**
- Import a revised draft as a new `ScriptVersion` (revision colour/date already in schema).
- Scene lineage across versions (match by preserved scene number, then by content
  similarity; A/B scene insertions, OMITTED scenes).
- Diff report: added / omitted / changed scenes, changed headings, cast added/removed,
  page-count deltas.
- Carry forward human-reviewed facts to unchanged scenes; re-queue facts in changed scenes.

**Scheduling & DOOD**
- Story days (script day 1, 2, …) — inferred, always reviewable.
- Shooting venues (`shooting_venues` table already exists) mapped to story locations/sets.
- Stripboard / one-liner, shooting schedule, Day-Out-of-Days (DOOD) per casting role
  (SW/W/H/WF/Travel), call-sheet seeds.
- Export compatible with Movie Magic Scheduling / StudioBinder formats where feasible.

**Known limitations to revisit (from Stage 1)**
- Live Claude extraction accuracy has not been measured in the build environment (no API key
  there). Measure it with `python -m app.cli accuracy` after a Claude run; the offline rules
  extractor scores 97% precision/recall on the sample but will do worse on messy real scripts.
- Extraction runs one request per scene; the Batch API (50% cost) is a possible later option.
- DOCX/FDX/Fountain page breaks are computed (~55 lines/page) and break between
  elements, so lengths can differ slightly from Final Draft's own pagination. FDX files that
  carry `SceneProperties` use Final Draft's own lengths instead.
- Dual dialogue in PDFs is typed by content with reduced confidence (flagged).
- Word documents with tables (e.g. dual dialogue in tables) import table text as UNKNOWN,
  placed at the end, flagged for review.

**Later / nice-to-have**
- Natural-language query → translated to the structured filter (filter shown, never hidden).
- Multi-user review with roles; Postgres deployment.
- Non-English / mixed-script support in OCR and extraction (e.g. Hindi/Devanagari dialogue).
