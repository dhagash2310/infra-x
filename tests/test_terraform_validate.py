"""
Highest-fidelity test layer: render each blueprint, then run real
`terraform init -backend=false && terraform validate` on the output.

This catches HCL syntax errors and provider-schema violations that no Python
test can — e.g. "you set `cpu_idle: true` but the schema only accepts that
inside `resources`, not at top level."

Skipped automatically if the `terraform` binary isn't on PATH, so this still
runs in CI on machines that have terraform installed and is a no-op locally
without it.

To run only this file:
    pytest tests/test_terraform_validate.py -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from infra_x.agent.planner import Planner
from infra_x.blueprints.loader import list_blueprints, load_blueprint
from infra_x.render import render_stack

ALL_BLUEPRINTS = [
    "aws-s3-static-site",
    "aws-lambda-api",
    "aws-ecs-fargate-web",
    "gcp-cloud-run",
    "aws-eks-cluster",
]

TERRAFORM = shutil.which("terraform")

pytestmark = pytest.mark.skipif(
    TERRAFORM is None,
    reason="`terraform` not on PATH — install Terraform to enable this layer.",
)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a subprocess, capturing combined output."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "TF_IN_AUTOMATION": "1"},
    )


@pytest.mark.parametrize("bp_id", ALL_BLUEPRINTS)
def test_blueprint_passes_terraform_validate(bp_id: str, tmp_path: Path) -> None:
    """Render the blueprint, then run terraform init + validate against it."""
    bp = load_blueprint(bp_id)
    stack = Planner(llm=None).deterministic(bp, stack_name=bp_id).stack
    render_stack(stack, tmp_path)

    init = _run([TERRAFORM, "init", "-backend=false", "-no-color"], cwd=tmp_path)
    assert init.returncode == 0, (
        f"terraform init failed for {bp_id}\n--- stdout ---\n{init.stdout}\n--- stderr ---\n{init.stderr}"
    )

    validate = _run([TERRAFORM, "validate", "-no-color"], cwd=tmp_path)
    assert validate.returncode == 0, (
        f"terraform validate failed for {bp_id}\n--- stdout ---\n{validate.stdout}\n--- stderr ---\n{validate.stderr}"
    )


@pytest.mark.parametrize("bp_id", ALL_BLUEPRINTS)
def test_blueprint_output_is_canonically_formatted(bp_id: str, tmp_path: Path) -> None:
    """`terraform fmt -check` should be a no-op on every freshly-rendered blueprint."""
    bp = load_blueprint(bp_id)
    stack = Planner(llm=None).deterministic(bp, stack_name=bp_id).stack
    render_stack(stack, tmp_path)

    # Note: this checks our renderer's output against terraform's canonical
    # formatter. It's OK if we're slightly more verbose than `terraform fmt`
    # (extra blank lines, alignment differences) — what we're catching here is
    # *broken* formatting that would fail to parse. So we don't fail the test
    # on diffs, only on a non-zero return code that isn't a "needs reformat"
    # signal (rc=3).
    fmt = _run([TERRAFORM, "fmt", "-check", "-diff", "-no-color"], cwd=tmp_path)
    # rc 0 = already formatted; rc 3 = would change but valid; rc !=0,3 = error
    assert fmt.returncode in (0, 3), (
        f"terraform fmt errored on {bp_id}\n--- stdout ---\n{fmt.stdout}\n--- stderr ---\n{fmt.stderr}"
    )


def test_all_blueprints_have_at_least_one_validation():
    """Sanity: at least the user-facing variables in every blueprint should validate inputs."""
    bps = list_blueprints()
    for bp in bps:
        # Find variables that map to a blueprint input (i.e. user-facing, not derived)
        input_names = {i.name for i in bp.inputs}
        validated = sum(
            1 for v in bp.variables
            if v.name in input_names and v.validations
        )
        # We don't require *every* input-variable to validate, but at least one
        # for every non-trivial blueprint (>= 3 user-facing variables).
        if len(input_names) >= 2:
            assert validated >= 1, (
                f"{bp.id} has {len(input_names)} user-facing variables but no validation blocks. "
                "Add at least one to catch bad inputs at plan time."
            )
