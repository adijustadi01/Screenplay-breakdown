# Screenplay Breakdown

A screenplay production breakdown tool. It reads a full feature script (PDF, DOCX,
Final Draft `.fdx`, Fountain), extracts what production needs to shoot it, and lets an
Assistant Director review, correct and query the results. Every fact links back to the
exact passage it came from; every count is computed in code.

- **[ROADMAP.md](ROADMAP.md)** — all three stages and current status
- **[DATA_MODEL.md](DATA_MODEL.md)** — the schema and the rules behind it

## Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11+, FastAPI | Best ecosystem for PDF/OCR/DOCX parsing and LLM SDKs |
| Database | SQLAlchemy 2 + SQLite (local), Postgres-ready; Alembic migrations | Zero-setup locally, one env var to ship |
| PDF | PyMuPDF | Text *and* line geometry (needed for 1/8-page lengths and margin scene numbers) |
| OCR | Tesseract via pytesseract | Local, free, gives per-word confidence for flagging |
| DOCX / FDX | python-docx, lxml | FDX is XML with typed paragraphs |
| Fountain | small in-house parser | Spec is simple; keeps element typing under our control |
| Extraction | Claude API (structured tool output) | Extraction only — never counting |
| Frontend | React + TypeScript + Vite | Review queue and drill-down tables |

## Layout

```
backend/
  app/
    db/               engine, session, declarative base
    models/           the data model (DATA_MODEL.md)
    importers/        PDF / DOCX / FDX / Fountain / OCR + coverage check
    scene_detection/  headings, scene numbers, page refs, 1/8-page lengths (no LLM)
    extraction/       per-scene LLM extraction (+ prompts/)
    reconciliation/   alias + location-variant merge proposals
    query/            composable structured filters
    reports/          deterministic reports and cross-counts
    review/           review queue workflow + audit log
    api/              FastAPI routers
  tests/
frontend/src/features/{import,scenes,review,reports,query}
samples/              sample script (6 formats) + ground truth + load-test generator
```

## Running locally

**Requirements:** Python 3.11+, Node 18+ (to build the UI once), and Tesseract for scanned PDFs
(`brew install tesseract` / `sudo apt install tesseract-ocr`; add language packs such as
`tesseract-ocr-hin` for Hindi). For Claude extraction you need an Anthropic API key.

### One-time setup

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

### Start the app

```bash
uvicorn app.main:app
```

Open http://localhost:8000. The workflow follows the tabs:

1. **Import & coverage**: create a project, upload the script, and read the coverage check.
2. Still on that tab, **run extraction** (Claude, or the offline rules extractor if you have no
   key), then **Find aliases & location variants**.
3. **Review queue**: flagged and low-confidence items come first, each beside its exact source
   passage. Confirm, edit, reject, accept or decline merges, and undo a merge if needed.
4. **Reports**: scenes, characters, locations, who appears together, and day/night cross-tabs.
   Click any number to open the scenes behind it.
5. **Query**: combine location, INT/EXT, time, character and department filters, and count by rows and columns.

**Stage 2 (departments):** on the Import tab, tick the departments and press **Run department
extraction** (after cast extraction). The **Departments** tab has a report per department plus a
**Continuity** timeline (costume and makeup states per character, scene by scene); every scene
view shows a **breakdown sheet**; Query adds "department elements" filters, e.g. EXT + NIGHT +
RAGHAV + motorcycle + rain.

### Command line (same engine)

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

Stage 2 commands:

```bash
python -m app.cli departments 1 --extractor claude --departments vehicles,props
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

Other reports are `scenes`, `locations`, `combinations`, `crosstab:character_time`,
`crosstab:location_time` and `crosstab:character_location_time`; add `--time effective` to
group CONTINUOUS/LATER scenes by their inherited lighting, and `--json` for raw output.
Extraction settings: `--model` (default `claude-opus-5-5`), `--effort` (default `medium`),
`--workers` (parallel requests, default 4). `--extractor heuristic` runs offline with no key.

### Run the tests

```bash
pytest
```

`pytest tests/test_stage1_acceptance.py -v` and `pytest tests/test_stage2_acceptance.py -v` run one
check per Stage 1 / Stage 2 requirement.

### How extraction stays honest

- **Code decides what code can decide.** Dialogue cues give who speaks, (V.O.)/(O.S.) and
  dialogue counts. If Claude disagrees, the code wins and the item is flagged.
- **Every quote is checked.** Claude must quote the script as evidence. A quote that isn't in
  the scene halves the confidence and sends the fact to review.
- **Merges are proposals.** Nothing is merged until you accept it, and accepted merges can be undone.
- **Your corrections are locked.** Re-running extraction never overwrites a fact you confirmed or edited.
- **Reports are queries.** A correction shows up in every report on the next click.

## Sample script and ground truth

`samples/monsoon_ledger.fountain` is a 15-scene, 7-page script with known contents, and
`samples/monsoon_ledger.ground_truth.json` is the hand-written answer key. The sample is
also provided as PDF, scanned (image-only) PDF, Final Draft FDX, styled DOCX and plain
DOCX (rebuild them with `python samples/build_samples.py`). It deliberately contains:

- CONTINUOUS and LATER headings (must stay as written, with lighting inherited and flagged)
- DUSK and DAWN (must not be collapsed to NIGHT/DAY)
- an inserted scene `8A`, an `OMITTED` scene, and one scene with no number
- a heading with no INT/EXT (`HOSPITAL ROOFTOP - NIGHT`)
- a location spelling variant (`ARJUNS HOUSE`)
- for Step 2: a villain who is only talked about, a silent grandmother, an (O.S.) child,
  a (V.O.) phone voice, Young/Adult Arjun, a cue alias (`INSPECTOR SHARMA` / `SHARMA`),
  and "a huge crowd" with no headcount

For load testing, `python samples/build_long_sample.py` builds a ~300-page synthetic script
in `samples/long/` (add `--scanned` for an image-only version).
