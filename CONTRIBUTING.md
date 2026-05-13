# Contributing to infra-x

Thanks for your interest. This is an early-stage project — the surface area is small, the issues list is short, and almost any reasonable contribution is welcome. This guide covers what kinds of changes we want most, how to get a working dev environment, and how to ship a PR that gets merged quickly.

## Where the leverage is

If you're looking for high-impact ways to help:

**New blueprints.** This is the single most useful contribution you can make. We currently ship five (S3 static site, Lambda API, ECS Fargate, Cloud Run, EKS); the world has hundreds of valuable patterns. The blueprint format is YAML, so you can write one without touching Python. See [BLUEPRINT_AUTHORING.md](BLUEPRINT_AUTHORING.md) for the full guide.

**Bug reports.** If `infra-x generate` produces Terraform that doesn't pass `terraform validate`, we want to know. File an issue with the blueprint ID, the prompt you used, and the terraform output.

**LLM provider expansion.** Bedrock, Gemini, and Mistral are all reasonable additions. The provider interface is a single `complete()` method; see `infra_x/llm/openai.py` for a 100-line template.

**Renderer improvements.** The HCL renderer is hand-written and covers the cases the bundled blueprints need. If you hit a Terraform construct it doesn't handle (dynamic blocks, complex `for_each`, etc.), open an issue or PR with a failing test.

**Documentation.** Better examples, clearer error messages, anything that makes the on-ramp easier.

## Dev environment setup

You need Python 3.10 or newer.

```bash
git clone https://github.com/infra-x/infra-x.git
cd infra-x
make dev          # creates .venv, installs in editable mode with [dev] extras
source .venv/bin/activate
```

The `Makefile` is the source of truth for every dev command:

```bash
make test              # pytest only
make verify            # pytest + blueprint validation + terraform validate (if installed)
make lint              # ruff check
make fmt               # ruff format
make update-snapshots  # refresh snapshot fixtures after intentional renderer changes
make build             # produce sdist + wheel
```

`make verify` is the gate we use before merging anything. If it passes locally, your PR will pass in CI.

## How tests are structured

Three layers, each catching a different class of bug:

1. **Unit tests** (`tests/test_*.py`) — IR validation, renderer correctness, backend parsing, LLM provider request shapes. These run on every save and are the fastest signal.
2. **Snapshot tests** (`tests/test_snapshots.py`) — every blueprint's rendered output is pinned to a fixture under `tests/snapshots/`. If a renderer change accidentally affects an existing blueprint, the diff fails the test loudly. After an intentional change, refresh with `make update-snapshots` and review the diff before committing.
3. **`terraform validate` layer** (`tests/test_terraform_validate.py`) — for every blueprint, runs `terraform init -backend=false && terraform validate` against the rendered output. Skipped automatically if the `terraform` binary isn't on your PATH; required in CI. This is the layer that catches real HCL bugs.

When you add a feature, write a test in the layer that's most likely to catch a regression. For renderer changes, that's usually a unit test in `tests/test_render.py` plus regenerated snapshots. For new blueprints, the snapshot + terraform-validate layers cover you automatically.

## PR checklist

Before opening a PR:

- `make verify` passes locally
- `make lint` passes (or run `make fmt` to auto-fix style)
- New behavior is covered by a test
- If you changed renderer output for an existing blueprint, you ran `make update-snapshots` and the diff is in your commit
- README / CHANGELOG updated if user-facing behavior changed

We aim for small, reviewable PRs. If your change is more than ~300 lines, consider breaking it into a sequence.

## Code style

- Python 3.10+ syntax (`X | Y` type unions, modern type hints)
- `from __future__ import annotations` at the top of every module
- Pydantic v2 for any data model
- `ruff` for linting and formatting (config is in `pyproject.toml`)
- Docstrings on public modules, classes, and non-obvious functions. We write them like notes to the next reader, not API reference; explain *why*, not just *what*.

## Reporting bugs

Open an issue. Include:

- The infra-x version (`infra-x version`)
- The exact command you ran
- The blueprint ID and the prompt (if you used one)
- The full output, including any error message
- What you expected to happen

If you can reproduce the bug with `--no-llm`, mention that — it rules out half the possible causes.

## Questions

Prefer GitHub Discussions for open-ended questions. Issues are for bugs and feature requests.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
