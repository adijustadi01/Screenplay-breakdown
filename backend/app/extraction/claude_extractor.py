"""Per-scene extraction with Claude (structured JSON output).

The stable instructions and the script-wide name lists go in the system prompt
with a cache breakpoint, so a 600-scene run pays for them once; only the scene
text changes per request.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import anthropic

from .schema import SceneExtraction, json_schema

PROMPT_VERSION = "scene-v1"
PROMPT = (Path(__file__).parent / "prompts" / "scene_v1.md").read_text(encoding="utf-8")
DEFAULT_MODEL = os.environ.get("BREAKDOWN_MODEL", "claude-opus-5-5")
DEFAULT_EFFORT = os.environ.get("BREAKDOWN_EFFORT", "medium")


class ExtractionRefused(RuntimeError):
    pass


class ClaudeExtractor:
    name = "claude"

    def __init__(self, model: str | None = None, effort: str | None = None, client=None):
        self.model = model or DEFAULT_MODEL
        self.effort = effort or DEFAULT_EFFORT
        self.client = client or anthropic.Anthropic(max_retries=4)
        self.prompt_version = PROMPT_VERSION
        self._context = ""

    def set_script_context(self, character_names, location_names, introduced_names=()) -> None:
        """Script-wide lists (identical for every scene -> cached)."""
        self._context = (
            "## Character names used as dialogue cues in this script\n"
            + (", ".join(sorted(character_names)) or "(none)")
            + "\n\n## Names introduced in CAPS in action lines\n"
            + (", ".join(sorted(set(introduced_names) - set(character_names))) or "(none)")
            + "\n\n## Locations named in scene headings\n"
            + ("\n".join(sorted(location_names)) or "(none)")
        )

    def extract(self, scene_text: str, ctx: dict | None = None) -> SceneExtraction:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.effort,
                           "format": {"type": "json_schema", "schema": json_schema()}},
            system=[
                {"type": "text", "text": PROMPT},
                {"type": "text", "text": self._context, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": f"Scene to break down:\n\n{scene_text}"}],
        )
        if response.stop_reason == "refusal":
            raise ExtractionRefused(getattr(response.stop_details, "explanation", None) or "refused")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("extraction output was cut off (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise RuntimeError("no text block in response")
        return SceneExtraction.model_validate(json.loads(text))
