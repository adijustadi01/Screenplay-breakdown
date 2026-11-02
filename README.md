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
samples/              sample script + ground truth (coming in Step 1)
```

## Running (current state: schema only)

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
pytest
```

Full run instructions (server + UI) arrive with Step 1.
