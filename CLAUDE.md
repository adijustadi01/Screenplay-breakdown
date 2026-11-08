# Handover notes for Claude

Read this first, then **README.md** (how it works and how to run it), **ROADMAP.md** (what is built and
what is still open) and **DATA_MODEL.md** (the schema).

## Who the user is
An Assistant Director / filmmaker. They are **not a programmer** and use Windows. Explain things in plain
language with exact clicks, and keep replies short. They want **no paid hosting and no team
deployment**: it is a single-user app, used as a Windows installer (or free GitHub Codespaces).

## What this is
A screenplay production-breakdown app. Stages 1–3 are complete:
- **Stage 1:** import (PDF/scan OCR/DOCX/FDX/Fountain) with a coverage check; scene, cast and location extraction; review queue; reports and query.
- **Stage 2:** nine departmental breakdowns.
- **Stage 3:** revision diff; story days vs shooting days; schedule, one-liner and DOOD.
- **Added later:** a one-click "Break down my script" job, a simple **Start here** screen and an **Excel export**
  (`backend/app/export/excel.py`, `GET /api/script-versions/{id}/export.xlsx`).

**Stack:**
- Backend: Python 3.11+, FastAPI, SQLAlchemy 2 (SQLite).
- Frontend: React and TypeScript with Vite (`frontend/`), served by FastAPI from `frontend/dist`.
- LLM: Claude via the `anthropic` SDK. Without an API key, the offline rules extractor is used.
- Desktop: a PyInstaller and Inno Setup installer built by `.github/workflows/windows-installer.yml` on every push
  (download it from the Actions run's artifacts). The Codespaces setup is in `.devcontainer/`.

## Rules that must not be broken (the user insisted on them)
1. **Never invent values.** An unknown headcount stays unknown, and CONTINUOUS is flagged and inherited.
   Ambiguity becomes a review item.
2. **The LLM extracts; code counts.** Every report and number is a deterministic query over reviewed data.
3. **Provenance on every fact**: the source passage, confidence, needs_review and review_status. Merges are proposals that
   can be undone, never silent.
4. **Human corrections are never overwritten** by re-extraction.
5. Keep **character vs casting requirement** and **story day vs shooting day** strictly separate
   (the DOOD uses shooting days and casting requirements).
6. Nothing is silently dropped at import (the coverage check).

## Run and test
```bash
cd backend && pip install -e ".[dev]" && python -m pytest -q            # 209 tests
cd frontend && npm ci && npm run build
cd backend && python -m uvicorn app.main:app --port 8000                  # open http://localhost:8000
```

## Still open
These come from ROADMAP.md:
- Measure live Claude accuracy on a real script (never run yet, because there was no key).
- PDF breakdown sheets.
- Calendar days off in the DOOD.
- Stripboard drag-and-drop, call sheets.
- Simplify the review queue wording further (the user finds technical labels confusing).
