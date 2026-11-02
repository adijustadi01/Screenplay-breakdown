"""All department specs, in breakdown-sheet order. Add a department by adding its module here."""
from __future__ import annotations

from importlib import import_module

from ..models.enums import Department
from .base import DepartmentSpec

MODULES = ["vehicles", "props", "costumes", "makeup_hair", "background", "animals",
           "action_effects", "sound_music", "production"]


def _load() -> dict[Department, DepartmentSpec]:
    specs: dict[Department, DepartmentSpec] = {}
    for m in MODULES:
        try:
            mod = import_module(f"{__package__}.{m}")
        except ModuleNotFoundError as exc:
            if exc.name == f"{__package__}.{m}":
                continue          # department not built yet
            raise
        specs[mod.SPEC.key] = mod.SPEC
    return specs


SPECS: dict[Department, DepartmentSpec] = _load()


def get(keys: list[str] | None = None) -> list[DepartmentSpec]:
    if not keys:
        return list(SPECS.values())
    out = []
    for k in keys:
        d = Department(k)
        if d not in SPECS:
            raise ValueError(f"department {k!r} is not available")
        out.append(SPECS[d])
    return out
