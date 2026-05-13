## What this PR does

One-paragraph summary.

## Why

What problem does this solve? Link to the issue if there is one (`Fixes #123`).

## How to test

Step-by-step. Reviewer should be able to copy-paste these commands.

## Checklist

- [ ] `make verify` passes locally (pytest + blueprint validation + terraform validate)
- [ ] `make lint` passes
- [ ] New behavior is covered by a test
- [ ] If renderer output changed for an existing blueprint, snapshots refreshed via `make update-snapshots` and the diff is in this PR
- [ ] README / CHANGELOG updated if user-facing behavior changed
- [ ] If this adds a new blueprint, it's in all three `ALL_BLUEPRINTS` lists (test_blueprints.py, test_snapshots.py, test_terraform_validate.py)
