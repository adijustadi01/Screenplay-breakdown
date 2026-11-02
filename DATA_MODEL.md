# Data model

The schema in `backend/app/models/` is the implementation of this document. If they
disagree, the code is wrong. Covered by `backend/tests/test_schema.py`.

## Design principles

1. **Two kinds of data, kept apart.**
   - *Source data* — what was imported: `script_versions → pages → script_elements`.
     It is never edited, only annotated.
   - *Extracted facts* — what we read out of the source: scenes, characters, aliases,
     casting roles, appearances, locations, scene↔location links. Every fact table
     uses the **ProvenanceMixin**.
2. **Story entities are not production entities.** A *Character* is someone in the story;
   a *CastingRole* is someone you hire. A *Location* is a place in the story; a
   *ShootingVenue* is where you film it.
3. **Uncertainty is stored, not resolved.** Unknown headcount = `NULL` + raw text + flag.
   Relative time = stored as written + a separate, flagged *inherited* value.
4. **Soft merges.** Merging two characters/locations sets `status=merged` and
   `merged_into_id`; nothing is deleted, so every merge can be inspected and undone.
5. **Reports read facts where `review_status != rejected`.** Unreviewed facts are counted
   but reports show how many of the contributing facts are still unreviewed.

## Entity diagram

```mermaid
erDiagram
    PROJECT ||--o{ SCRIPT_VERSION : has
    SCRIPT_VERSION ||--o{ PAGE : has
    SCRIPT_VERSION ||--o{ SCRIPT_ELEMENT : has
    PAGE ||--o{ SCRIPT_ELEMENT : contains
    SCRIPT_VERSION ||--o{ SCENE : has
    SCENE ||--o{ SCRIPT_ELEMENT : "owns (coverage)"
    PROJECT ||--o{ CHARACTER : has
    CHARACTER ||--o{ CHARACTER_ALIAS : "known as"
    CHARACTER ||--o{ CASTING_ROLE : "realised by"
    CASTING_ROLE |o--o{ CASTING_ROLE : "doubles"
    SCENE ||--o{ APPEARANCE : has
    CHARACTER ||--o{ APPEARANCE : in
    CASTING_ROLE |o--o{ APPEARANCE : "covers"
    PROJECT ||--o{ LOCATION : has
    LOCATION |o--o{ LOCATION : "parent of"
    LOCATION ||--o{ LOCATION_ALIAS : "spelled as"
    SCENE ||--o{ SCENE_LOCATION : at
    LOCATION ||--o{ SCENE_LOCATION : hosts
    SHOOTING_VENUE |o--o{ LOCATION : "shot at (later)"
    PROJECT ||--o{ REVIEW_ITEM : queue
    PROJECT ||--o{ REVIEW_EVENT : "audit log"
    SCRIPT_VERSION ||--o{ EXTRACTION_RUN : "processed by"
```

## Provenance — on every extracted fact

| Column | Meaning |
|---|---|
| `source_scene_id` | Scene the fact was read from |
| `source_element_start_id` / `_end_id` | Exact range of script elements (lines) supporting it |
| `source_excerpt` | Verbatim quote of the supporting text |
| `extracted_by` | `parser` · `ocr` · `llm` · `rules` (offline extractor) · `reconciliation` · `human` |
| `extraction_run_id` | Which run (model + prompt version) produced it |
| `confidence` | 0.0–1.0 (DB-enforced range) |
| `needs_review` + `review_reason` | Flag + machine-readable reason(s), e.g. `continuous_time` |
| `review_status` | `unreviewed` · `confirmed` · `edited` · `rejected` |
| `reviewed_at` / `reviewed_by` | Who signed it off |

`QuantityMixin` (`quantity_min`, `quantity_max`, `quantity_raw`) is used wherever a count
appears. "FOUR GOONS" → 4/4. "a huge crowd" → NULL/NULL + raw text + `needs_review`.

## Source tables

**projects** — one production. Characters and locations belong to the project (not the
script version) so they survive revisions in Stage 3.

**script_versions** — one imported file: filename, format, SHA-256, page count, status
(`importing → imported → extracted → reconciled`), revision label/colour/date (Stage 3).

**pages** — every physical (PDF) or computed (DOCX/FDX/Fountain) page: `page_index`
(physical order), `page_label` (printed number, e.g. "23A"), `text_source`
(`text_layer`/`ocr`/`structured`), `raw_text`, `ocr_mean_confidence`, `is_title_page`.

**script_elements** — every typed block of text in order (`scene_heading`, `action`,
`character`, `parenthetical`, `dialogue`, `transition`, `shot`, `page_header`,
`title_page`, `non_printing` (Fountain notes/sections), `unknown`), with page, owning scene, raw + normalised text, line count and
PDF y-coordinates (for 1/8-page lengths). **Coverage proof:** every element is either in
a scene, is non-scene furniture, or produces a review item.

**extraction_runs** — every automated pass (import, extraction, reconciliation) with
model, prompt version, params, stats, errors.

## Scenes

| Field | Rule |
|---|---|
| `seq` | Order in the script (1..N). Internal only. |
| `scene_number` | **Original number, verbatim** ("23", "23A", "A23"). `NULL` if the script has none — never generated. |
| `scene_number_right` | Right-margin number when it differs from the left |
| `number_status` | `ok` · `missing` · `mismatch` · `duplicate` · `out_of_order` |
| `is_omitted` | "23 OMITTED" placeholders are kept as scenes |
| `heading_raw`, `heading_status` | `ok` · `ambiguous` · `inferred` (no heading found; boundary guessed → always review) |
| `int_ext` + `int_ext_raw` | `INT` · `EXT` · `INT/EXT` · `UNKNOWN` |
| `location_raw` | Location text from the heading, verbatim |
| `time_of_day` + `time_of_day_raw` | **As written**: `DAY NIGHT DAWN DUSK SUNRISE SUNSET MORNING AFTERNOON EVENING CONTINUOUS SAME LATER MOMENTS LATER UNSPECIFIED OTHER` |
| `effective_time_of_day` | For relative times only: lighting inherited from the nearest earlier absolute-time scene; `effective_time_source_scene_id` says which one; `effective_time_needs_review` is always true until confirmed |
| `start/end_page_id`, `start/end_page_label` | Source page references |
| `length_eighths` + `length_method` | Integer eighths (13 = 1 5/8 pp). `pdf_geometry` (vertical extent on the real page), `line_count` (computed pagination for DOCX/FDX/Fountain) or `source_document` (Final Draft's own lengths when the .fdx carries them). Omitted scenes are 0. |
| `synopsis` | Short description (LLM), with run id |

Provenance on a scene covers the **heading parse** (source = the heading element).

## Characters, casting, appearances

**characters** — a story entity: `canonical_name`, `kind` (`individual` / `group`),
description, soft-merge fields. Groups use `QuantityMixin` for headcount.

**character_aliases** — every name used for the character (cue names and names in
action), normalised. Linking an alias to a different character is a reconciliation
proposal in the review queue.

**casting_roles** — a hiring need for a character: `kind` = `actor` · `voice` ·
`stunt_double` · `photo_double` · `body_double`; `name` ("Young Arjun"), `age_note` as
written, `doubles_role_id` for doubles.
*"Young Arjun" and "Adult Arjun" = one Character, two `actor` CastingRoles.*

**appearances** — one row per (scene, character, casting role):

| `presence` | Meaning | Counts as an appearance? |
|---|---|---|
| `present` | Physically in the scene — **speaking or silent** | **Yes** |
| `off_screen` | (O.S.)/(O.C.): in the scene's space, unseen | **Yes** (shown separately — see decision 1) |
| `voice_only` | (V.O.), phone/radio voice; not physically there | No — voice casting need |
| `mentioned` | Only talked about | **Never** — stored so the AD sees it was considered |
| `uncertain` | Text doesn't say | Not until reviewed; always `needs_review` |

`speaking` = `speaking` · `silent` · `unknown`. `dialogue_block_count` is counted by the
**parser** from dialogue cues — not by the LLM; cue extensions (V.O.)/(O.S.) also set
`presence` in code and override the model if it disagrees (flagged). `name_as_written` keeps
how this scene refers to the character; `version_note` keeps an age/version cue ("10",
"young") as evidence for casting roles. For GROUP characters the per-scene headcount lives
on the appearance (`quantity_min/max/raw`, NULL = unknown, flagged).

## Locations

**locations** — self-referencing tree of any depth: `parent_id`, `kind`
(`story_location` / `set`), soft-merge fields, and `venue_id` (empty until the locations
department fills it).

```
ARJUN'S HOUSE (story_location)
 ├─ KITCHEN (set)
 └─ BEDROOM (set)
CITY HOSPITAL (story_location)
 ├─ ICU (set)
 └─ CORRIDOR (set)
```

**location_aliases** — every heading spelling mapped to a location ("ARJUNS HOUSE -
KITCHEN", "KITCHEN - ARJUN'S HOUSE").

**scene_locations** — scene ↔ location with `role` = `primary` · `intercut` ·
`secondary`. A scene links to its most specific location (the set); reports can roll up
to the parent.

**shooting_venues** — real-world places. Exists now so Stage 3 needs no restructuring.

## Stage 2 — departmental breakdowns (`backend/app/models/breakdown.py`)

One generic model serves all nine departments; each department is a spec module in
`backend/app/departments/` (categories, character roles, detail fields, extraction
instructions, offline rules, department review rules, report extras).

| Table | Meaning |
|---|---|
| **breakdown_items** | The thing's *identity* across the script: `department`, `name` (normalised, unique per department), `category` (e.g. `motorcycle`, `weapon`, `uniform`), owner `character_id`, `attributes`, soft-merge fields, provenance. |
| **breakdown_occurrences** | The item *in one scene*: `QuantityMixin` (stated headcount; NULL = unknown, flagged), `description`, department-specific `details` (validated against the spec: e.g. vehicles `moving/action`, costumes `state`, effects `practical`/`vfx_candidate`, songs `playback`), `is_inferred` (not stated — always reviewed), provenance. Unique per (scene, item). |
| **occurrence_characters** | Who is involved and how: `role` from the department's list (driver, passenger, user, wearer, performer, singer, handler...). |

**Identity vs quantity.** One recurring jeep in 10 scenes = one item, 10 occurrences of
quantity 1. Three police jeeps together = one item, one occurrence of quantity 3. Reports
give scenes per item, max units in one scene, and the department's peak units on set at once.

**Departments:** `vehicles`, `props`, `costumes`, `makeup_hair`, `background`, `animals`,
`action_effects`, `sound_music`, `production`.

**Code-derived items** (certain, `extracted_by=parser`): voiceovers from (V.O.) cues, songs from
`SONG:` lines. **Rule-derived production requirements** (`extracted_by=rules`, `is_inferred=true`,
always "INFERRED — review"): rain → rain towers, moving vehicle with actors → camera rig, large or
unknown crowd → crowd management, animals → handler/welfare, weapons/gunfire → armourer, stunts →
stunt coordinator, fire/explosion → SFX & fire safety, playback song → choreographer & playback,
under-18 → permits & chaperone, heights → rigging, water → water safety.

Department items go through the same review queue (`entity_table = breakdown_occurrences`),
reconciliation (`merge_item` proposals, undoable), reports and query engine (`elements` filters).

## Review queue and audit log

**review_items** — one queue for everything (`category` = `import` · `extraction` ·
`reconciliation`), with `reason_code`, `severity`, message, a polymorphic pointer to the
fact (`entity_table`, `entity_id`) so Stage 2 tables plug in unchanged, direct links to
the source (`scene_id`, `page_id`, `element_id`), and an optional machine-applicable
`proposal` (e.g. `{"action":"merge_character","from_id":12,"into_id":3}`).
Status `open → resolved/dismissed` with resolution and note.

**review_events** — append-only audit log: entity, action
(confirm/edit/reject/create/merge/unmerge), before/after JSON snapshots, actor, time.

### How a correction propagates
Reports are queries, not stored numbers. When the AD rejects an appearance, its
`review_status` becomes `rejected`, an event is logged, and every report that reads
appearances simply stops counting it on the next request. No cache to invalidate.

## Decisions (approved)

1. **O.S. counts as an appearance** (actor needed on set), shown in its own column so it can be
   excluded; V.O. never counts as on set.
2. **Relative times** (CONTINUOUS/SAME/LATER/MOMENTS LATER): reports group by the as-written value
   by default, with a toggle to group by the inherited value (marked as inferred).
3. **Scenes link to the most specific location** (the set); reports roll up to the parent.
4. **Group characters** with dialogue are characters with a per-scene headcount (unknown stays
   unknown); non-speaking crowds wait for Stage 2 background.
5. **Page lengths**: PDF from real geometry; DOCX/FDX/Fountain from standard pagination (~55
   lines/page), or Final Draft's own figures when the .fdx includes them.
