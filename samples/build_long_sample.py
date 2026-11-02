"""Build a ~300-page synthetic script (Fountain + PDF + scanned PDF) for load testing.

    python samples/build_long_sample.py [repeats=45]

Outputs go to samples/long/ (git-ignored). Scenes are renumbered 1..N.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_samples as bs  # noqa: E402

repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 45
src = (HERE / "monsoon_ledger.fountain").read_text()
head, body = src.split("FADE IN:", 1)
body = body.rsplit("FADE OUT.", 1)[0]
n = 0
chunks = []
for r in range(repeats):
    def renum(m):
        global n
        n += 1
        return f"#{n}#"
    chunk = re.sub(r"#[0-9A-Z]+#", renum, body)
    chunks.append(chunk)
out = HERE / "long"
out.mkdir(exist_ok=True)
fountain = out / "long_synthetic.fountain"
fountain.write_text(head + "FADE IN:\n" + "".join(chunks) + "\nFADE OUT.\n")
bs.SRC = fountain
title, els = bs.load()
pages = bs.build_pdf(title, els, out / "long_synthetic.pdf")
print(f"{fountain.name}: {n} numbered scenes; long_synthetic.pdf: {pages} pages")
if "--scanned" in sys.argv:
    bs.build_scanned(out / "long_synthetic.pdf", out / "long_synthetic_scanned.pdf", dpi=200)
    print("long_synthetic_scanned.pdf written")
