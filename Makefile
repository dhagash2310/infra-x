.PHONY: install dev test lint fmt clean run-example reset check-python verify update-snapshots build release-check publish-test publish

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# Pick the interpreter: env var > python3.12 > python3.11 > python3.10 > python3.
# Override with `PYTHON=python3.12 make dev` if you want a specific one.
PYTHON ?= $(shell command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)

check-python:
	@if [ -z "$(PYTHON)" ]; then \
		echo ">> No usable python3 found. Install Python 3.10+ (e.g. 'brew install python@3.12')"; \
		exit 1; \
	fi
	@PYVER=$$( "$(PYTHON)" -c 'import sys; print("%d.%d" % sys.version_info[:2])' ); \
	REQ_OK=$$( "$(PYTHON)" -c 'import sys; print(sys.version_info >= (3, 10))' ); \
	if [ "$$REQ_OK" != "True" ]; then \
		echo ">> $(PYTHON) is Python $$PYVER, but infra-x needs >= 3.10."; \
		echo "   Install a newer Python and re-run:  brew install python@3.12"; \
		echo "   Then:  PYTHON=python3.12 make dev"; \
		exit 1; \
	fi; \
	echo ">> Using $(PYTHON) (Python $$PYVER)"

# Detect a stale/broken venv (e.g. shebang points at a path that no longer exists)
# and rebuild from scratch instead of failing inscrutably.
define ensure_clean_venv
	@if [ -d "$(VENV)" ] && ! "$(VENV)/bin/python3" --version >/dev/null 2>&1; then \
		echo ">> Detected broken $(VENV) — wiping and rebuilding"; \
		rm -rf "$(VENV)"; \
	fi
endef

install: check-python
	$(call ensure_clean_venv)
	"$(PYTHON)" -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e .

dev: check-python
	$(call ensure_clean_venv)
	"$(PYTHON)" -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

reset:
	rm -rf $(VENV) .pytest_cache
	@echo ">> Cleared $(VENV) and .pytest_cache. Run 'make dev' to rebuild."

test:
	$(PY) -m pytest

lint:
	$(VENV)/bin/ruff check .

fmt:
	$(VENV)/bin/ruff format .

run-example:
	$(VENV)/bin/infra-x generate --blueprint aws-s3-static-site \
		--out ./examples/out/static-site \
		--no-llm \
		--overwrite

# Comprehensive pre-commit / pre-push gate. Runs:
#  0. ruff lint (catches the same things CI catches)
#  1. unit + snapshot tests
#  2. blueprint validation (loader + IR + renderer round-trip)
#  3. terraform validate against every blueprint, IF terraform is on PATH
verify: dev
	@echo ">> Layer 0: ruff lint"
	$(VENV)/bin/ruff check .
	@echo ""
	@echo ">> Layer 1: pytest"
	$(VENV)/bin/pytest -q
	@echo ""
	@echo ">> Layer 2: infra-x validate (blueprint + IR + renderer)"
	$(VENV)/bin/infra-x validate
	@echo ""
	@if command -v terraform >/dev/null 2>&1; then \
		echo ">> Layer 3: terraform init+validate per blueprint"; \
		rm -rf .verify; \
		for bp in aws-s3-static-site aws-lambda-api gcp-cloud-run aws-ecs-fargate-web aws-eks-cluster; do \
			echo ">>   $$bp"; \
			$(VENV)/bin/infra-x generate -b $$bp --no-llm --out .verify/$$bp >/dev/null; \
			(cd .verify/$$bp && terraform init -backend=false -no-color >/dev/null 2>&1 && terraform validate -no-color) || exit 1; \
		done; \
		rm -rf .verify; \
		echo ">> All blueprints pass terraform validate."; \
	else \
		echo ">> Layer 3 skipped: terraform not on PATH (install for full coverage)"; \
	fi
	@echo ""
	@echo ">> verify OK"

update-snapshots: dev
	INFRAX_UPDATE_SNAPSHOTS=1 $(VENV)/bin/pytest tests/test_snapshots.py -v

clean:
	rm -rf $(VENV) build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} +

# --- Release pipeline -------------------------------------------------------
# build         : produce sdist + wheel under ./dist/
# release-check : install the freshly-built wheel into a throwaway venv and
#                 confirm the CLI runs and the bundled blueprints are present
# publish-test  : upload to TestPyPI (https://test.pypi.org) — dry run for real publish
# publish       : upload to PyPI for real (requires PYPI_TOKEN env var or ~/.pypirc)

build: dev
	rm -rf dist build *.egg-info
	$(PY) -m build
	@echo ">> Built artifacts:"
	@ls -lh dist/

release-check: build
	@echo ">> Smoke-test the wheel in a throwaway venv"
	@rm -rf .release-check
	"$(PYTHON)" -m venv .release-check
	.release-check/bin/pip install --upgrade pip >/dev/null
	.release-check/bin/pip install dist/*.whl
	.release-check/bin/infra-x version
	.release-check/bin/infra-x list-blueprints
	.release-check/bin/infra-x validate
	@rm -rf .release-check
	@echo ">> release-check OK — wheel is shippable"

publish-test: release-check
	$(PY) -m twine upload --repository testpypi dist/*
	@echo ">> Uploaded to TestPyPI. Try:  pip install -i https://test.pypi.org/simple/ infra-x"

publish: release-check
	@echo ">> About to upload to PyPI. Ctrl-C in 5s to abort."
	@sleep 5
	$(PY) -m twine upload dist/*
	@echo ">> Published. Try:  pipx install infra-x"
