"""Department extraction with Claude: one request per scene covering the selected departments."""
from __future__ import annotations

import json
from pathlib import Path

import anthropic

from ..extraction.claude_extractor import DEFAULT_EFFORT, DEFAULT_MODEL, ExtractionRefused
from .base import DepartmentSpec, ItemOut, SceneContext, item_schema

PROMPT_VERSION = "dept-v1"
GENERAL = (Path(__file__).parent / "prompts" / "general_v1.md").read_text(encoding="utf-8")


def _department_prompt(spec: DepartmentSpec) -> str:
    details = "\n".join(f"  - `{d.name}`: {d.description}" + (f" (one of: {', '.join(d.choices)})" if d.choices else "")
                        for d in spec.details)
    return (f"## {spec.label} (`{spec.key.value}`)\n\n{spec.prompt.strip()}\n\n"
            f"Categories: {', '.join(spec.categories)}, other.\n"
            f"Character roles: {', '.join(spec.roles)}.\n"
            f"Details per scene:\n{details or '  (none)'}\n")


class DepartmentClaudeExtractor:
    name = "claude"

    def __init__(self, specs: list[DepartmentSpec], model: str | None = None, effort: str | None = None,
                 client=None):
        self.specs = specs
        self.model = model or DEFAULT_MODEL
        self.effort = effort or DEFAULT_EFFORT
        self.client = client or anthropic.Anthropic(max_retries=4)
        self.prompt_version = PROMPT_VERSION + "+" + ",".join(s.key.value for s in specs)
        self.instructions = GENERAL + "\n\n" + "\n".join(_department_prompt(s) for s in specs)
        props = {s.key.value: item_schema(s) for s in specs}
        self.schema = {"type": "object", "additionalProperties": False,
                       "properties": {"departments": {"type": "object", "additionalProperties": False,
                                                      "properties": props, "required": list(props)}},
                       "required": ["departments"]}
        self._context = ""

    def set_script_context(self, cast: list[str], locations: list[str]) -> None:
        self._context = ("## Cast list (use these exact names)\n" + (", ".join(sorted(cast)) or "(none)")
                         + "\n\n## Locations\n" + ("\n".join(sorted(locations)) or "(none)"))

    def extract(self, scene_text: str, ctx: SceneContext) -> dict[str, list[ItemOut]]:
        facts = (f"Already established for this scene: {ctx.int_ext}, {ctx.time}, at {ctx.location_label}. "
                 f"On set: {', '.join(sorted(ctx.present)) or 'nobody recorded'}."
                 + (f" Voice only: {', '.join(sorted(ctx.voice_only))}." if ctx.voice_only else ""))
        response = self.client.beta.messages.create(
            model=self.model, max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": self.schema}},
            system=[{"type": "text", "text": self.instructions},
                    {"type": "text", "text": self._context, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"{facts}\n\nScene to break down:\n\n{scene_text}"}],
        )
        if response.stop_reason == "refusal":
            raise ExtractionRefused(getattr(response.stop_details, "explanation", None) or "refused")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("department output was cut off (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise RuntimeError("no text block in response")
        data = json.loads(text)["departments"]
        return {k: [ItemOut(**it) for it in v] for k, v in data.items()}
