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
- **Added later:** a one-click "Break down my script" job, a simple **Start here** screen, and
  **Excel and PDF export of chosen parts**: `backend/app/export/sections.py` builds the tables, and `excel.py`
  and `pdf.py` (reportlab) write them. Routes: `GET /api/script-versions/{id}/export.xlsx|.pdf?sections=a,b`
  and `.../export/sections`. In the UI, the download window is `frontend/src/components/ExportDialog.tsx`,
  opened from the top-bar button, which is always visible.
- **Built-in dictionary (no API key):** `backend/app/lexicon/` holds Princeton WordNet nouns (built by
  `backend/tools/build_lexicon.py`) so the free reader knows people from objects ("A MIRROR" is not cast;
  "A FAKIR", "THE GOONS" are). Any no-dialogue "character" the dictionary calls a thing is flagged
  `looks_like_object`, on the Claude path too. `EXTRA_PEOPLE` / `EXTRA_GROUPS` there add Indian/film words.
- **Age-based casting roles:** `backend/app/reconciliation/ages.py` reads ages from the text
  ("ARJUN (6)" … "TWENTY YEARS LATER" … "ARJUN (28)"), carries them forward until a time jump, and proposes
  one casting role per age group (one character, separate actors). Proposal action `age_roles`, undoable.
  Age names: baby/child/teen/young (20–39)/middle-aged (40–59)/old (60+). The AD picks which ages share an
  actor when accepting (`changes={"actors": ["A","B","B"]}` → `merge_plans`). Character report rows carry
  `by_role` (scenes per actor); export section `cast_by_role`; Scenes tab has a "Played by" picker
  (edit `casting_role_id`, validated to the same character). Re-extraction keeps chosen roles (`prev_roles`).
  The user insists: the app suggests, the director decides.
  Samples: `samples/river_years.fountain`, `samples/three_ages.fountain`; tests: `backend/tests/test_ages_and_dictionary.py`.

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
cd backend && pip install -e ".[dev]" && python -m pytest -q            # 222 tests
cd frontend && npm ci && npm run build
cd backend && python -m uvicorn app.main:app --port 8000                  # open http://localhost:8000
```

## Still open
These come from ROADMAP.md:
- Measure live Claude accuracy on a real script (never run yet, because there was no key).
- Calendar days off in the DOOD.
- Stripboard drag-and-drop, call sheets.
- Simplify the review queue wording further (the user finds technical labels confusing).
