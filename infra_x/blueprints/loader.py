"""
Blueprint loader.

A blueprint is a YAML file describing a recipe for one kind of stack. It carries:

  - metadata (id, name, description, provider, est. cost / time)
  - inputs: variables the user can supply
  - services / connections: the IR template (with `${var.foo}` placeholders)
  - agent_guidance: free-form text shown to the LLM during customization

The loader keeps blueprints close to data: minimal logic, lots of validation, so
contributors can author them without reading our codebase.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from infra_x.ir.models import (
    Connection,
    Output,
    ProviderRequirement,
    Service,
    Stack,
    Variable,
)


class BlueprintInput(BaseModel):
    """A user-facing input to the blueprint."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any | None = None
    examples: list[str] = Field(default_factory=list)


class Blueprint(BaseModel):
    """Parsed YAML blueprint."""

    id: str
    name: str
    description: str
    provider: str = "aws"
    region: str | None = None
    version: str = "0.1.0"
    estimated_cost_usd_monthly: tuple[float, float] | None = None
    estimated_setup_minutes: tuple[int, int] | None = None
    tags: list[str] = Field(default_factory=list)
    inputs: list[BlueprintInput] = Field(default_factory=list)

    # IR template — fields here are passed through to the Stack model.
    variables: list[Variable] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)
    outputs: list[Output] = Field(default_factory=list)
    extra_providers: list[ProviderRequirement] = Field(default_factory=list)

    # Auxiliary files written next to the .tf output — e.g. placeholder Lambda
    # source code, scripts, sample data. Map of relative-path -> file contents.
    companion_files: dict[str, str] = Field(default_factory=dict)

    # Free-form text the planner agent gets in its system prompt.
    agent_guidance: str = ""

    def to_stack(self, name: str) -> Stack:
        """Materialize the blueprint into a concrete Stack with deterministic defaults."""
        return Stack(
            name=name,
            provider=self.provider,
            region=self.region,
            description=self.description,
            blueprint_id=self.id,
            blueprint_version=self.version,
            variables=list(self.variables),
            services=[s.model_copy(deep=True) for s in self.services],
            connections=list(self.connections),
            outputs=list(self.outputs),
            extra_providers=list(self.extra_providers),
        )


# --- loading -----------------------------------------------------------------


def _catalog_dir() -> Path:
    """Path to the bundled catalog directory."""
    # Works both when installed and when running from source.
    return Path(__file__).parent / "catalog"


def list_blueprints() -> list[Blueprint]:
    """Load every blueprint in the bundled catalog."""
    out: list[Blueprint] = []
    for p in sorted(_catalog_dir().glob("*.yaml")):
        out.append(_load_file(p))
    return out


def load_blueprint(blueprint_id: str) -> Blueprint:
    """Load a blueprint by id (filename without extension)."""
    p = _catalog_dir() / f"{blueprint_id}.yaml"
    if not p.exists():
        # tolerate `.yml`
        alt = _catalog_dir() / f"{blueprint_id}.yml"
        if alt.exists():
            p = alt
        else:
            raise FileNotFoundError(
                f"blueprint {blueprint_id!r} not found in {_catalog_dir()}"
            )
    return _load_file(p)


def _load_file(path: Path) -> Blueprint:
    raw = yaml.safe_load(path.read_text())
    return Blueprint.model_validate(raw)
