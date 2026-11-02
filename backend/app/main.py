"""FastAPI application: uvicorn app.main:app --reload

If the frontend has been built (frontend/dist), it is served at / as well,
so a single command runs the whole tool.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import breakdown, scripts, stage3

app = FastAPI(title="Screenplay Breakdown", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"],
                   allow_headers=["*"])
app.include_router(scripts.router)
app.include_router(breakdown.router)
app.include_router(stage3.router)

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(DIST / "index.html")
