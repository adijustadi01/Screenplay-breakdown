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

## Stage 2 — Departmental breakdowns  *(complete — awaiting your test on a real script)*

One generic model (`breakdown_items` identity / `breakdown_occurrences` per scene /
`occurrence_characters` roles) and one module per department. Same provenance, confidence,
needs_review, review queue, reconciliation, reports and query engine as Stage 1.

| Department | Module | Highlights | Status |
|---|---|---|---|
| Framework | `departments/base.py`, `service.py`, `reports.py` | spec contract, shared extraction + verification, peak units, continuity, scene sheet, query `elements` | ✅ |
| Vehicles | `vehicles.py` | identity vs quantity, drivers/passengers/owner, moving/action | ✅ |
| Props | `props.py` | users/holders, weapon & consumable flags, hero props | ✅ |
| Costumes | `costumes.py` | per character, changes, uniform/period/dance, wet/torn/bloodied → multiples | ✅ |
| Makeup & hair | `makeup_hair.py` | injury/ageing/prosthetic/disguise/hairstyle, fresh → bandaged continuity, gap checks | ✅ |
| Background | `background.py` | stated headcounts only, unknown flagged, large-crowd/featured flags | ✅ |
| Animals | `animals.py` | species, trained behaviour, mandatory handler assessment | ✅ |
| Action & effects | `action_effects.py` | separate practical / VFX-candidate flags, safety & stunt-double review | ✅ |
| Songs, dance & sound | `sound_music.py` | songs (playback/lip-sync/live), dancers, instruments, scripted sounds; V.O. + SONG: derived in code | ✅ |
| Production requirements | `production.py` | stated needs + rule-derived needs, always "INFERRED — review" | ✅ |
| UI | Departments tab, scene breakdown sheet, Import controls, Query element filters | | ✅ |

**Stage 2 acceptance:** `pytest tests/test_stage2_acceptance.py -v`.
**Sample:** `samples/highway_song.fountain` + `highway_song.ground_truth.json` (70+ department items);
score with `python -m app.cli dept-accuracy <version> ../samples/highway_song.ground_truth.json`.

**Still open (Stage 2):** live Claude accuracy on the department sample and on a real script;
~~export~~ ✅ Excel and PDF export; the user picks which parts to download (PDF has one breakdown sheet per scene).

## Stage 3 — Revision tracking + scheduling  *(complete — awaiting your test on a real script)*

| Part | Module | Status |
|---|---|---|
| Scene matching between drafts (number + content, renumbered/unnumbered, manual re-match, review flags) | `revisions/matcher.py`, `service.py` | ✅ |
| Carry-forward of reviewed facts for unchanged scenes; extract only changed/added scenes | `revisions/service.py` | ✅ |
| Revision report: scenes changed; per scene cast (characters) vs casting roles, locations, all departments; script-wide; scheduled scenes affected; coverage-aware ("not compared yet") | `revisions/diff.py` | ✅ |
| Story days (inferred, flagged, editable) — separate from shooting days | `scheduling/story_days.py` | ✅ |
| Shooting days, scene assignment, split scenes, reorder, auto-fill suggestion, schedule carried through a revision | `scheduling/service.py` | ✅ |
| DOOD (SW/W/WF/SWF/H, drop/pickup) over shooting days; rows = casting requirements; one-liner with cast IDs + story days alongside | `scheduling/reports.py` | ✅ |
| UI: Revisions tab, Schedule tab (story vs shooting panel, one-liner, DOOD, story-day editing) | `frontend/src/features/{revisions,schedule}` | ✅ |

**Stage 3 acceptance:** `pytest tests/test_stage3_acceptance.py -v`.
**Sample:** `samples/monsoon_ledger_rev_blue.fountain` + `monsoon_ledger_rev_blue.expected.json`.

**Not built yet (candidates for later):** calendar days off / weekends and travel days in the DOOD;
multiple units; stripboard drag-and-drop; call sheets; Movie Magic / StudioBinder export; reading
revision asterisks and coloured pages from PDFs as extra evidence; (Excel and PDF export ✅ done).

**Added after Stage 3**
- ✅ Built-in dictionary (WordNet) for the free reader: people vs objects, animals and sounds (`app/lexicon`).
- ✅ Age-based casting roles: one character at clearly different ages → one casting role per age, proposed
  for review (`app/reconciliation/ages.py`).

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
