# PyInstaller recipe for the desktop app.   Run from the repository root:
#     pyinstaller desktop/screenplay_breakdown.spec --noconfirm
# Needs frontend/dist built first (cd frontend && npm ci && npm run build).
# Optional: set TESSERACT_DIR to a Tesseract install folder to bundle OCR.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

hidden = (collect_submodules("app") + collect_submodules("uvicorn") + collect_submodules("anthropic")
          + collect_submodules("reportlab") + ["pymupdf", "lxml.etree", "lxml._elementpath", "pytesseract", "PIL.Image"])
datas = [
    (os.path.join(ROOT, "backend", "app", "extraction", "prompts"), "app/extraction/prompts"),
    (os.path.join(ROOT, "backend", "app", "departments", "prompts"), "app/departments/prompts"),
    # Built-in English dictionary (people vs things) for the free reader.
    (os.path.join(ROOT, "backend", "app", "lexicon", "nouns.tsv.gz"), "app/lexicon"),
    (os.path.join(ROOT, "backend", "app", "lexicon", "WORDNET_LICENSE.txt"), "app/lexicon"),
    (os.path.join(ROOT, "frontend", "dist"), "frontend_dist"),
    (os.path.join(ROOT, "samples", "monsoon_ledger.fountain"), "samples"),
    (os.path.join(ROOT, "samples", "highway_song.fountain"), "samples"),
    (os.path.join(ROOT, "samples", "monsoon_ledger_rev_blue.fountain"), "samples"),
    (os.path.join(ROOT, "samples", "river_years.fountain"), "samples"),
    (os.path.join(ROOT, "samples", "three_ages.fountain"), "samples"),
]
datas += collect_data_files("docx") + collect_data_files("reportlab")
tess = os.environ.get("TESSERACT_DIR")
if tess and os.path.isdir(tess):
    datas.append((tess, "tesseract"))

a = Analysis(
    [os.path.join(ROOT, "desktop", "launcher.py")],
    pathex=[os.path.join(ROOT, "backend")],
    hiddenimports=hidden,
    datas=datas,
    # Optional extras PyMuPDF / pytesseract use only if present — not needed here.
    excludes=["tkinter", "pytest", "matplotlib", "numpy", "pandas", "onnxruntime", "scipy", "IPython",
              "playwright", "uvloop"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ScreenplayBreakdown", console=False,
          icon=os.environ.get("SB_ICON") or None)
coll = COLLECT(exe, a.binaries, a.datas, name="ScreenplayBreakdown")
