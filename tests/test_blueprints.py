"""Tests for blueprint loading and the deterministic planner path."""

from __future__ import annotations

import pytest

from infra_x.agent.planner import Planner
from infra_x.blueprints.loader import list_blueprints, load_blueprint
from infra_x.render import HCLRenderer

ALL_BLUEPRINTS = [
    "aws-s3-static-site",
    "aws-lambda-api",
    "aws-ecs-fargate-web",
    "gcp-cloud-run",
    "aws-eks-cluster",
]


def test_catalog_has_five_blueprints():
    """v0 ships with 5 starter blueprints."""
    bps = list_blueprints()
    ids = {b.id for b in bps}
    assert set(ALL_BLUEPRINTS).issubset(ids), f"missing: {set(ALL_BLUEPRINTS) - ids}"


@pytest.mark.parametrize("bp_id", ALL_BLUEPRINTS)
def test_every_blueprint_loads_and_renders(bp_id: str):
    bp = load_blueprint(bp_id)
    assert bp.id == bp_id
    assert bp.services, f"{bp_id} has no services"

    # Deterministic planner should always produce a valid stack.
    result = Planner(llm=None).deterministic(bp, stack_name=bp_id)
    files = HCLRenderer(result.stack).files()

    # Core files always present
    assert "versions.tf" in files
    assert "provider.tf" in files
    assert "main.tf" in files

    # Every service id should appear *somewhere* in the rendered output —
    # categorized services live in <category>.tf, the rest in main.tf.
    all_resource_text = "\n".join(
        content for name, content in files.items()
        if name not in ("versions.tf", "provider.tf", "variables.tf", "outputs.tf", "backend.tf")
    )
    for svc in result.stack.services:
        assert f'"{svc.id}"' in all_resource_text, f"missing resource for {svc.id} in {bp_id}"


@pytest.mark.parametrize("bp_id", ALL_BLUEPRINTS)
def test_blueprint_categorizes_services(bp_id: str):
    """Every service in every blueprint should have a category set (no `other` leftovers)."""
    bp = load_blueprint(bp_id)
    uncategorized = [s.id for s in bp.services if s.category == "other"]
    assert not uncategorized, (
        f"{bp_id} has uncategorized services: {uncategorized}. "
        "Set `category:` on each service so the renderer can split files cleanly."
    )


def test_load_unknown_blueprint_raises():
    with pytest.raises(FileNotFoundError):
        load_blueprint("does-not-exist")


def test_lambda_api_is_self_contained():
    """The lambda-api blueprint should ship its placeholder source + extra provider so it validates out of the box."""
    bp = load_blueprint("aws-lambda-api")

    # archive provider declared
    assert any(p.local_name == "archive" for p in bp.extra_providers), (
        "lambda-api needs hashicorp/archive in extra_providers for archive_file"
    )

    # placeholder Lambda source ships as a companion file
    assert "lambda_src/index.js" in bp.companion_files, (
        "lambda-api needs lambda_src/index.js as a companion file"
    )

    # the data.archive_file block exists in the IR
    data_blocks = [s for s in bp.services if s.kind == "data"]
    assert any(s.type == "archive_file" for s in data_blocks), (
        "lambda-api needs a `data archive_file` Service to feed Lambda's filename"
    )
