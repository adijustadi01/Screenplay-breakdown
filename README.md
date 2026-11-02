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

Requirements: Python 3.11+, and Tesseract for scanned PDFs
(`brew install tesseract` / `sudo apt install tesseract-ocr`; add language packs such as
`tesseract-ocr-hin` for Hindi).

```bash
cd backend
```

```bash
python -m venv .venv && source .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

### Import a script from the command line

```bash
python -m app.cli import ../samples/monsoon_ledger.pdf --project "Monsoon Ledger"
```

This prints the **coverage check**: every page, every character of text, every scene and
every flagged item. Then list the detected scenes:

```bash
python -m app.cli scenes 1
```

Other useful commands: `python -m app.cli coverage 1` (re-print the check, `--json` for
machine-readable), `--ocr-lang eng+hin` on import for mixed-language scans, and `--db`
(or `$DATABASE_URL`) to choose the database. The default is `./breakdown.db`.

### Run the API

```bash
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs to try the endpoints: upload a script, get the coverage
check, list scenes, and open a scene with its exact source text.

### Run the tests

```bash
pytest
```

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
