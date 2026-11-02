# Screenplay Breakdown

A production breakdown tool for feature screenplays (300–400 pages). It reads the script,
extracts what production needs to shoot it, and gives an Assistant Director the tools to review,
correct, query, revise and schedule it.

- **Stage 1 — Foundation.** Import any script format, detect scenes, and extract cast and
  locations. Every fact has a review queue, and reports, cross-counts and queries are computed in code.
- **Stage 2 — Departments.** Vehicles, props, costumes, makeup & hair, background, animals,
  action & effects, songs/dance/sound, and production requirements.
- **Stage 3 — Revisions and scheduling.** Compare drafts and see what changed. Group scenes into
  shooting days and get a Day-Out-of-Days for cast.

Further reading:

- **[ROADMAP.md](ROADMAP.md)**: what is built, what is open, and the rules behind it.
- **[DATA_MODEL.md](DATA_MODEL.md)**: the schema, which is the most important design decision.

## The rules it follows

1. **Every fact is traceable.** Each extracted item stores the exact source passage, a confidence
   and a `needs_review` flag. Clicking any number opens the scenes behind it.
2. **Nothing is invented.** "A huge crowd" means the headcount is *unknown* and flagged; it is
   never given a number. CONTINUOUS stays CONTINUOUS, with the inherited lighting stored separately
   and flagged. A missing scene number is flagged, never generated. Inferred production needs say
   **"INFERRED — review"**.
3. **The LLM extracts; code counts.** All reports, cross-counts, DOODs and revision diffs are
   computed deterministically from the stored facts.
4. **Nothing is silently dropped.** The coverage check accounts for every page and every
   character of text.
5. **Your corrections are final.** Confirmed or edited facts are never overwritten by re-extraction
   and are carried into revised drafts. Every change is in the audit log, and every merge can be undone.
6. **Two distinctions are kept strict throughout:**
   - **Character vs casting requirement.** "Young Arjun" and "Adult Arjun" are *one character* but
     *two casting requirements*. Voice-only and doubles are separate requirements too. The DOOD
     rows are casting requirements.
   - **Story day vs shooting day.** Story days are when scenes happen in the plot: inferred, flagged
     and editable. Shooting days are when they are filmed, set by you. Thirty night scenes may be
     two story nights and five shooting nights. The DOOD uses shooting days only.

## Windows app (installer)

You can install this as a normal Windows program instead of using the developer setup below.

1. **Get the installer.** Open the repository's **Actions** tab on GitHub → **Windows installer** →
   the latest green run. Download **ScreenplayBreakdown-Windows**, which contains
   `ScreenplayBreakdown-Setup.exe` and a portable zip.
2. **Run `ScreenplayBreakdown-Setup.exe`.** No administrator rights are needed. The installer is
   not code-signed, so Windows SmartScreen may say "Windows protected your PC": click **More info →
   Run anyway**.
3. **Start Screenplay Breakdown** from the Start menu or the desktop shortcut. A small console
   window opens and the app opens in your browser. Close the console window to quit.
4. **Enter your Anthropic API key** on the Import tab to use Claude extraction. It is stored only on
   your computer.

Details:

- Your projects are saved in `%APPDATA%\ScreenplayBreakdown` and kept if you uninstall or upgrade.
- OCR for scanned PDFs (Tesseract) is included.
- The sample scripts are in the Start-menu folder.
- The installer is rebuilt automatically when the code changes (see
  `.github/workflows/windows-installer.yml`). Every build runs the full test suite on Windows and a
  self-test of the built program (`ScreenplayBreakdown.exe --smoke`) before the installer is made.
- To build it yourself on a Windows PC: build the UI (`npm ci && npm run build` in `frontend`), run
  `pip install -e "backend[dev]" pyinstaller`, run `pyinstaller desktop/screenplay_breakdown.spec`,
  then compile `desktop/installer.iss` with Inno Setup.

## Setup (developers)

**Requirements:**
- Python 3.11+
- Node 18+ (to build the UI once)
- Tesseract, for scanned PDFs (`brew install tesseract` / `sudo apt install tesseract-ocr`, plus
  language packs such as `tesseract-ocr-hin` for Hindi)
- An Anthropic API key, for Claude extraction. Everything else works without one, and the offline
  rules extractor is a lower-accuracy fallback.

```bash
cd backend
```

```bash
python -m venv .venv && source .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

```bash
cd ../frontend && npm install && npm run build && cd ../backend
```

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

```bash
uvicorn app.main:app
```

Open **http://localhost:8000**. The database is `backend/breakdown.db` (SQLite); set
`DATABASE_URL` to use Postgres instead.

## Using it — the tabs, in workflow order

| Tab | Stage | What you do there |
|---|---|---|
| **Import & coverage** | 1–2 | Create a project and upload a script (PDF, scanned PDF, DOCX, FDX, Fountain). Read the **coverage check**. Run **extraction** (Claude or offline), **Find aliases & location variants**, then **department extraction** (tick departments). |
| **Scenes** | 1–2 | Every scene with its exact text, cast (present / O.S. / V.O. / mentioned; speaking / silent) and its **breakdown sheet** for all departments. Correct anything in place, or add what was missed. |
| **Review queue** | all | Errors and warnings first, then the lowest confidence. Each item sits beside its highlighted source passage. Confirm, edit, reject, accept or decline merges, undo, or act in bulk. |
| **Reports** | 1 | Scenes (INT/EXT × time, pages), characters (scenes, O.S., V.O., mentioned, speaking, dialogue, first/last, casting roles), locations with sub-location roll-up, who appears together, and day/night cross-tabs. Choose time *as written* or *with inherited lighting*. |
| **Departments** | 2 | One report per department (identity vs quantity, peak units on set, characters and roles, department columns) plus a **Continuity** timeline of costume and makeup states per character, with gap checks. |
| **Query** | 1–2 | Combine filters: location (including sub-locations), INT/EXT, time, characters (all/any), and **department elements**. Example: *EXT + NIGHT + RAGHAV + motorcycle + rain*. Count by rows and columns; every cell opens its scenes. |
| **Revisions** | 3 | Import the revised draft into the same project (it becomes current), then **Compare** with the previous draft. Unchanged scenes keep your reviewed facts; extract only the changed/added scenes with one click. See which scenes changed and, per scene, the cast (characters) and casting roles, locations and department items added, removed or re-quantified. Also shown: the text diff, script-wide changes, and scheduled scenes affected. Doubtful matches can be re-matched by hand. |
| **Schedule** | 3 | **Infer story days**, which you can correct in the Story days table. Create a schedule and add shooting days (with dates), or click **Suggest days** for a starting point grouped by location and day/night. Assign scenes from the unscheduled pool; scenes can be split across days. The **Story time vs shooting time** panel, **one-liner** and **DOOD** follow. **Create missing casting roles** firms up implicit DOOD rows, and **Carry schedule forward** moves a schedule onto a revised draft. |

### What each department extracts (Stage 2)

| Department | Highlights |
|---|---|
| Vehicles | Identity vs quantity: one recurring jeep in 10 scenes ≠ 10 jeeps. Drivers, passengers, owner, moving/action. Peak vehicles on set at once. |
| Props | Who uses them, where and when; weapon (armourer) and consumable flags; hero props. |
| Costumes | Per character; changes; uniform, period and dance wear; continuity states (wet / torn / bloodied) that need multiples. |
| Makeup & hair | Injuries, ageing, prosthetics, disguises, hairstyle changes; continuity (fresh → bandaged), with gap checks. |
| Background | Groups with stated headcounts only; unknown crowds flagged; large-crowd and featured flags. |
| Animals | Species and trained behaviour; every animal flagged for handler/welfare assessment. |
| Action & effects | Fights, falls, chases, crashes, fire, rain, explosions; **separate** practical vs potential-VFX flags (undecided is flagged, not guessed). |
| Songs, dance & sound | Songs (playback / lip-sync / live), dancers, instruments, scripted sounds. Voiceovers come from (V.O.) cues and songs from `SONG:` lines, both read by code. |
| Production requirements | Stated needs (crane, drone, built sets) plus rule-inferred needs (rain towers, camera rigs, crowd management, armourer, stunt coordinator, SFX/fire safety, child permits, rigging, water safety), always "INFERRED — review". |

### The DOOD (Stage 3)

- **Rows are casting requirements, numbered by workload (cast ID 1 is the busiest):**
  - Requirements marked *implicit* belong to characters with no casting role yet; create one.
  - Requirements marked *unassigned* belong to characters with several roles where the scene
    doesn't say which.
  - Voice-only requirements and background groups are listed separately.
- **Columns are shooting days.**
- **Codes:**
  - SW: start work
  - W: work
  - WF: work finish
  - SWF: single day
  - H: hold
  - WD / PW: drop and pick-up around a gap longer than the schedule's `drop_after_days` (default 10)

## Command line (same engine as the UI)

Run these from the `backend` folder. Stage 1:

```bash
python -m app.cli import ../samples/monsoon_ledger.pdf --project "Monsoon Ledger"
```

```bash
python -m app.cli extract 1 --extractor claude
```

```bash
python -m app.cli reconcile 1
```

```bash
python -m app.cli report 1 characters
```

```bash
python -m app.cli accuracy 1 ../samples/monsoon_ledger.ground_truth.json
```

Stage 2:

```bash
python -m app.cli departments 1 --extractor claude
```

```bash
python -m app.cli dept-report 1 vehicles
```

```bash
python -m app.cli dept-report 1 continuity
```

```bash
python -m app.cli dept-accuracy 1 ../samples/highway_song.ground_truth.json
```

Stage 3 (import the revised draft into the same project first, then compare v1 with v2):

```bash
python -m app.cli import ../samples/monsoon_ledger_rev_blue.fountain --project "Monsoon Ledger"
```

```bash
python -m app.cli revise 1 2 --extractor claude --departments all
```

```bash
python -m app.cli story-days 2
```

```bash
python -m app.cli schedule 2 --create "Main unit" --autofill 40
```

More options:

- **Reports:** `scenes`, `locations`, `combinations`, `crosstab:character_time`,
  `crosstab:location_time`, `crosstab:character_location_time`. Add `--time effective` for
  inherited lighting and `--json` for raw output.
- **Department reports:** any department name, or `continuity`.
- **Extraction:** `--model` (default `claude-opus-5-5`), `--effort` (default `medium`), `--workers`
  (default 4), and `--departments vehicles,props` to limit which departments run.
- **Other commands:** `coverage <version>`, `scenes <version>`, `revision-report <id>`, and
  `schedule <version> --schedule <id>` to print an existing schedule.
- **Offline:** `--extractor heuristic` runs without an API key.

## Samples and ground truth

| File | Purpose |
|---|---|
| `samples/monsoon_ledger.fountain` (+ PDF, scanned PDF, FDX, DOCX, plain DOCX) | 15 scenes covering Stage 1 traps: CONTINUOUS/LATER, DUSK/DAWN, 8A, OMITTED, an unnumbered scene, a heading with no INT/EXT, a location spelling variant, a villain who is only mentioned, a silent grandmother, O.S. and V.O., Young/Adult Arjun, a cue alias, and "a huge crowd". Answer key: `monsoon_ledger.ground_truth.json`. |
| `samples/highway_song.fountain` | 8 scenes for every department: one recurring jeep plus three jeeps together, a song with 200 dancers, animals, a fight, fall, explosion and rain, period costume, ageing, prosthetics, disguise, continuity states. Answer key: `highway_song.ground_truth.json`. |
| `samples/monsoon_ledger_rev_blue.fountain` | A revised draft: a cut character, changed dialogue, Sharma and a jeep added, a car-to-taxi location change, an omitted scene and a new 9A. Expected diff: `monsoon_ledger_rev_blue.expected.json`. |
| `samples/build_long_sample.py` | Builds a ~305-page synthetic script (`--scanned` for an image-only version) for load testing. |

Rebuild the format variants with `python samples/build_samples.py`.

## Tests

```bash
pytest
```

The suite covers more than 200 tests. Each stage also has a short acceptance file with one test
per requirement in its brief:

```bash
pytest tests/test_stage1_acceptance.py tests/test_stage2_acceptance.py tests/test_stage3_acceptance.py -v
```

## Measured performance (in the build environment)

- 305-page text PDF: import and coverage check in about 4 seconds.
- 305-page scanned PDF: OCR import in about 2 min 40 s (parallel Tesseract); every scene found,
  every character accounted for.
- Extraction: one Claude request per scene, run in parallel (`--workers`). The script-wide context
  is prompt-cached.

## Known limits

- **Claude accuracy is unmeasured here.** It hasn't been measured on real scripts in the build
  environment, which had no API key. Use the `accuracy` and `dept-accuracy` commands on the samples,
  then spot-check a real script.
- **The offline extractor is a baseline.** It was tuned on the samples, can't resolve pronouns or
  tell a photograph from a person, and flags everything it finds.
- **Pagination of non-PDF formats is computed.** DOCX/FDX/Fountain pages are computed at ~55
  lines/page, so lengths may differ slightly from Final Draft's own figures, unless the .fdx
  carries them.
- **Scheduling is a foundation, not a full scheduler.** There are no calendar days off or travel
  days in the DOOD yet, no multiple units, no stripboard drag-and-drop, no call sheets and no export
  formats. See ROADMAP.md.

## Project layout

```
backend/app/
  importers/        PDF (text + OCR) / DOCX / FDX / Fountain, coverage check
  scene_detection/  headings, scene numbers, pages, 1/8-page lengths
  extraction/       Stage 1 per-scene extraction (Claude + offline), verification, accuracy
  reconciliation/   alias / casting-variant / location / item merge proposals (undoable)
  departments/      Stage 2: one module per department + shared service, reports, accuracy
  revisions/        Stage 3: scene matching, carry-forward, revision diff
  scheduling/       Stage 3: story days, shooting days, one-liner, DOOD
  reports/          Stage 1 reports and the shared fact view
  query/            composable query engine (incl. department elements)
  review/           review queue and audit log
  api/              FastAPI routers
  models/           the data model
frontend/src/features/  import, scenes, review, reports, departments, query, revisions, schedule
samples/                sample scripts, answer keys, generators
```
