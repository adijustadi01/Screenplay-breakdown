"""Score stored department items against a department ground-truth JSON.

    python -m app.cli dept-accuracy <version_id> ../samples/highway_song.ground_truth.json
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..extraction.cues import normalize_name
from ..reports.facts import load_facts
from . import registry


def evaluate_departments(session: Session, version_id: int, truth_path: str | Path,
                         departments: list[str] | None = None) -> dict[str, Any]:
    truth = json.loads(Path(truth_path).read_text())
    f = load_facts(session, version_id)
    wanted = set(departments or [d.value for d in registry.SPECS])
    by_seq = {s.seq: s for s in f.scenes}
    occ_by_scene = defaultdict(list)
    for o in f.occurrences:
        occ_by_scene[o.scene_id].append(o)
    stats = defaultdict(lambda: {"expected": 0, "found": 0, "qty_ok": 0, "qty_n": 0, "chars_ok": 0, "chars_n": 0,
                                 "inferred_ok": 0, "inferred_n": 0, "extracted": 0})
    problems = []
    for ts in truth["scenes"]:
        sc = by_seq.get(ts["seq"])
        if sc is None:
            continue
        used = set()
        occs = [o for o in occ_by_scene.get(sc.id, []) if o.department in wanted]
        for o in occs:
            stats[o.department]["extracted"] += 1
        for ti in ts["items"]:
            d = ti["department"]
            if d not in wanted:
                continue
            st = stats[d]
            st["expected"] += 1
            hit = None
            # Prefer a match on the item's name; fall back to its description.
            for field in ("name", "all"):
                for o in occs:
                    if o.department != d or o.id in used:
                        continue
                    hay = o.name.lower() if field == "name" else \
                        f"{o.name} {o.description or ''} {o.category or ''}".lower()
                    if any(k.lower() in hay for k in ti["match"]):
                        hit = o
                        break
                if hit:
                    break
            if hit is None:
                problems.append(f"sc {ts['number']} {d}: MISSED {ti['name']}")
                continue
            used.add(hit.id)
            st["found"] += 1
            if ti["qty"] is not None or ti.get("qty_text"):
                st["qty_n"] += 1
                if hit.quantity_max == ti["qty"]:
                    st["qty_ok"] += 1
                else:
                    problems.append(f"sc {ts['number']} {d}: {ti['name']} quantity {hit.quantity_max} vs truth {ti['qty']}")
            for cname, role in ti["chars"].items():
                st["chars_n"] += 1
                got = {(f.characters[c].canonical_name, r) for c, r in hit.characters if c in f.characters}
                if (normalize_name(cname), role) in got or (hit.owner_id in f.characters and
                                                            f.characters[hit.owner_id].canonical_name == cname):
                    st["chars_ok"] += 1
                else:
                    problems.append(f"sc {ts['number']} {d}: {ti['name']} missing {cname} as {role}")
            if ti.get("inferred"):
                st["inferred_n"] += 1
                if hit.inferred and hit.needs_review:
                    st["inferred_ok"] += 1
                else:
                    problems.append(f"sc {ts['number']} {d}: {ti['name']} should be flagged inferred — review")
        for o in occs:
            if o.id not in used:
                problems.append(f"sc {ts['number']} {o.department}: extra {o.name} (not in truth — check)")

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None

    per = {d: {"recall": pct(s["found"], s["expected"]), "expected": s["expected"], "found": s["found"],
               "extracted": s["extracted"], "precision_vs_truth": pct(s["found"], s["extracted"]),
               "quantity_accuracy": pct(s["qty_ok"], s["qty_n"]), "character_accuracy": pct(s["chars_ok"], s["chars_n"]),
               "inferred_flagged": pct(s["inferred_ok"], s["inferred_n"])}
           for d, s in sorted(stats.items())}
    return {"departments": per, "problems": problems}


def format_dept_accuracy(r: dict) -> str:
    lines = ["DEPARTMENT ACCURACY vs ground truth",
             f"  {'department':16} {'recall':>7} {'prec.':>7} {'qty':>7} {'chars':>7} {'inferred':>9}"]
    for d, s in r["departments"].items():
        fmt = lambda v: "—" if v is None else f"{v}%"  # noqa: E731
        lines.append(f"  {d:16} {fmt(s['recall']):>7} {fmt(s['precision_vs_truth']):>7} "
                     f"{fmt(s['quantity_accuracy']):>7} {fmt(s['character_accuracy']):>7} {fmt(s['inferred_flagged']):>9}")
    lines += ["", f"  {len(r['problems'])} discrepancies:", *[f"    - {p}" for p in r["problems"]]]
    return "\n".join(lines)
