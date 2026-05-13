"""
The Planner.

Given (a) a chosen blueprint and (b) a user's free-form requirements, the
planner asks the LLM to fill in the variable defaults — and only that, for v0.
We deliberately do NOT yet let the LLM mutate `services` or `connections`:
that's where hallucinations cause real damage, and v0 should prove the
deterministic-renderer pipeline works first.

In v0.2 we'll widen the planner to:
  - propose new services (still constrained by an allow-list)
  - emit suggested connections
  - explain its reasoning per-step (for the canvas timeline view)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from infra_x.blueprints.loader import Blueprint
from infra_x.ir.models import Stack
from infra_x.llm.base import LLMProvider


@dataclass
class PlannerResult:
    stack: Stack
    notes: str
    raw_llm_response: str | None = None


SYSTEM_PROMPT = """You are infra-x, an expert infrastructure-as-code assistant. \
Your job is to take a user's plain-English requirements and a chosen blueprint, \
and decide values for the blueprint's input variables. You do NOT invent new \
resources. You do NOT change the blueprint's structure. You only return JSON \
matching the schema you are given.

Rules:
1. Use the user's exact wording for names where reasonable (lowercased, hyphenated).
2. If the user doesn't specify a value, prefer the blueprint default.
3. Never include explanatory prose in the JSON output.
4. Never invent variables that are not in the schema.
5. If a value is uncertain, fall back to the documented default and note it."""


def _slugify(s: str) -> str:
    """Make a string safe for use as a Terraform name / S3 bucket name."""
    out = "".join(c.lower() if c.isalnum() else "-" for c in s)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:50] or "infra-x-stack"


class Planner:
    """Maps prompt -> filled Stack IR."""

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm

    # --- Deterministic mode ----------------------------------------------------

    def deterministic(self, blueprint: Blueprint, stack_name: str) -> PlannerResult:
        """No LLM — just materialize the blueprint with defaults."""
        stack = blueprint.to_stack(name=_slugify(stack_name))
        return PlannerResult(
            stack=stack,
            notes="Generated deterministically from blueprint defaults (no LLM).",
        )

    # --- LLM-assisted mode -----------------------------------------------------

    def from_prompt(
        self,
        blueprint: Blueprint,
        prompt: str,
        stack_name: str | None = None,
    ) -> PlannerResult:
        """LLM-customized variable values, then materialize the stack."""
        if self.llm is None:
            raise RuntimeError("No LLM provider configured. Use `--no-llm` or set one up.")

        # Build a JSON schema describing what we want back.
        schema = self._var_schema(blueprint)
        user_msg = self._build_user_msg(blueprint, prompt, schema)

        resp = self.llm.complete(
            system=SYSTEM_PROMPT,
            user=user_msg,
            json_mode=True,
            temperature=0.2,
        )
        if resp.parsed is None:
            raise RuntimeError(
                "LLM did not return valid JSON. "
                f"First 500 chars of response: {resp.content[:500]!r}"
            )

        chosen: dict[str, Any] = resp.parsed.get("variables", {}) if isinstance(resp.parsed, dict) else {}
        notes: str = resp.parsed.get("notes", "") if isinstance(resp.parsed, dict) else ""

        # Apply chosen values onto the blueprint's variable defaults.
        merged_vars = []
        for v in blueprint.variables:
            new_default = chosen.get(v.name, v.default)
            merged_vars.append(v.model_copy(update={"default": new_default}))

        # Pick a stack name: explicit > LLM-suggested > blueprint id.
        chosen_name = stack_name or (
            chosen.get("__stack_name__")
            if isinstance(chosen.get("__stack_name__"), str)
            else None
        )
        final_name = _slugify(chosen_name or blueprint.id)

        stack = blueprint.to_stack(name=final_name)
        stack.variables = merged_vars

        return PlannerResult(stack=stack, notes=notes, raw_llm_response=resp.content)

    # --- helpers ---------------------------------------------------------------

    def _var_schema(self, blueprint: Blueprint) -> dict[str, Any]:
        """Build a JSON-schema-ish description of the blueprint's inputs."""
        props: dict[str, Any] = {}
        for v in blueprint.variables:
            props[v.name] = {
                "tf_type": v.type,
                "description": v.description or "",
                "default": v.default,
            }
        return {
            "type": "object",
            "properties": {
                "variables": {
                    "type": "object",
                    "properties": props,
                    "additionalProperties": False,
                },
                "notes": {
                    "type": "string",
                    "description": "Brief rationale for the chosen values.",
                },
            },
            "required": ["variables"],
        }

    def _build_user_msg(
        self,
        blueprint: Blueprint,
        prompt: str,
        schema: dict[str, Any],
    ) -> str:
        guidance = (blueprint.agent_guidance or "").strip()
        return f"""\
Blueprint: {blueprint.id} — {blueprint.name}
Description: {blueprint.description}

User requirements:
\"\"\"
{prompt.strip()}
\"\"\"

Blueprint guidance for you:
\"\"\"
{guidance}
\"\"\"

Available variables (with their defaults):
{json.dumps(schema['properties']['variables']['properties'], indent=2)}

Return ONLY a JSON object of the form:
{{
  "variables": {{ "<var_name>": <value>, ... }},
  "notes": "<one-paragraph rationale>"
}}

Only include variables you want to change. Omit any you want left at default.
"""
