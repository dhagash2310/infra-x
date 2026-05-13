# infra-x End-to-End Test Plan

Three scenarios that together cover every core path through the system. Run them in order. Total time: ~25 minutes if everything is set up; ~45 if you're installing Ollama / Terraform from scratch.

| # | Scenario | Time | What it proves |
|---|---|---|---|
| 1 | Deterministic generation + new features | ~7 min | IR + renderer + multi-file split + backend + validations all wired correctly. **No external dependencies.** |
| 2 | LLM-powered generation with local Ollama | ~10 min | Planner agent → Ollama → customized variable values → rendered output. The core hypothesis. |
| 3 | Full `make verify` gate | ~3 min | Test suite, blueprint validation, and (if installed) terraform-validate all pass green. CI-ready. |

---

## One-time setup

Do this once before running any scenario.

```bash
# 1. Modern Python (skip if you already have 3.10+)
brew install python@3.12

# 2. Wipe any stale venv and rebuild
cd "/Users/dhagash/Documents/Projects/Claude Projects/Test/Terraform Gui/infra-x"
sudo rm -rf .venv .pytest_cache 2>/dev/null
PYTHON=python3.12 make dev
source .venv/bin/activate

# 3. Verify the CLI is on PATH
infra-x version
# Expected: infra-x 0.0.1
```

If `infra-x version` doesn't print, your venv didn't activate or the install failed — re-run `make dev` and check the output.

**Optional but recommended:**

```bash
# Terraform itself — needed for Scenarios 1 (steps 4-5) and 3 (Layer 3)
brew install terraform
terraform version
# Expected: Terraform v1.x.x
```

---

# Scenario 1 — Deterministic generation + all new features

**Goal:** Prove that without any LLM, the renderer correctly produces multi-file output, validation blocks, and backend configuration. Plus prove the validations actually work at `terraform plan` time.

**Prerequisites:** One-time setup. `terraform` on PATH (steps 4-5 only).

## Step 1.1 — Inspect the blueprint catalog

```bash
infra-x list-blueprints
```

**Expected:** A Rich-formatted table showing 5 blueprints. The `Cloud` column should show `aws` (4) and `gcp` (1). The `Est. cost / mo` column should show ranges.

**Pass criteria:**
- ✅ All 5 blueprints listed
- ✅ Costs and descriptions present
- ✅ No errors

## Step 1.2 — Generate with categories + backend (the new flagship path)

```bash
rm -rf ./out/lambda-api
infra-x generate \
  --blueprint aws-lambda-api \
  --no-llm \
  --backend "s3://my-tfstate-acme/lambda-api/prod.tfstate?region=us-east-1&lock=tf-locks" \
  --out ./out/lambda-api
```

**Expected output panel:**
```
✓ Generated aws-lambda-api (aws, 11 resources)
Backend: s3
Notes: Generated deterministically from blueprint defaults (no LLM).
```

**Verify the multi-file split:**
```bash
ls ./out/lambda-api/
```

**Expected files:**
```
backend.tf       ← NEW: the remote state backend
compute.tf       ← Lambda function
database.tf     ← DynamoDB table
iam.tf           ← Roles + policy attachments
main.tf          ← Header only (no uncategorized resources)
networking.tf   ← API Gateway + integration + route + stage
observability.tf ← CloudWatch log group
outputs.tf
provider.tf
security.tf      ← Lambda permission
variables.tf    ← Includes validation blocks
versions.tf
```

**Pass criteria:**
- ✅ At least 5 category files exist (compute, database, iam, networking, observability)
- ✅ `backend.tf` exists and is non-empty
- ✅ `main.tf` exists but contains no `resource` blocks (header only)

## Step 1.3 — Inspect the backend block

```bash
cat ./out/lambda-api/backend.tf
```

**Expected:**
```hcl
terraform {
  backend "s3" {
    bucket  = "my-tfstate-acme"
    key     = "lambda-api/prod.tfstate"
    region  = "us-east-1"
    encrypt = true
    dynamodb_table = "tf-locks"
  }
}
```

**Pass criteria:**
- ✅ `bucket`, `key`, `region`, `encrypt = true`, `dynamodb_table` all present
- ✅ Values match exactly what was passed to `--backend`

## Step 1.4 — Inspect a validation block

```bash
grep -A 4 'validation {' ./out/lambda-api/variables.tf | head -30
```

**Expected:** Several `validation { ... }` blocks. For example:
```hcl
validation {
  condition     = can(regex("^[a-z][a-z0-9-]{1,30}[a-z0-9]$", var.api_name))
  error_message = "api_name must be 3-32 chars, lowercase letters/digits/hyphens."
}

validation {
  condition     = var.lambda_memory_mb >= 128 && var.lambda_memory_mb <= 10240
  error_message = "lambda_memory_mb must be between 128 and 10240 (AWS Lambda limit)."
}
```

**Pass criteria:**
- ✅ At least 5 validation blocks across the variables file
- ✅ Each has both `condition =` and `error_message =`

## Step 1.5 — Confirm Terraform accepts the generated HCL

(Skip this step if you don't have `terraform` installed.)

```bash
cd ./out/lambda-api
terraform init -backend=false      # -backend=false skips the S3 backend init for this smoke test
terraform validate
```

**Expected:**
```
Initializing the backend...
Initializing provider plugins...
- Finding hashicorp/aws versions matching "~> 5.0"...
- Installing hashicorp/aws ...
Terraform has been successfully initialized!

Success! The configuration is valid.
```

**Pass criteria:**
- ✅ `terraform init` completes without errors
- ✅ `terraform validate` says "Success! The configuration is valid."

## Step 1.6 — Prove validations actually fire on bad input

This is the killer test: ensure that the validation blocks **reject bad inputs at plan time** rather than letting AWS reject them later.

> ⚠️ **Note:** the stack from Step 1.2 has a `backend.tf` pointing at the placeholder bucket `my-tfstate-acme`, which doesn't exist. `terraform plan` will refuse to run until the backend is initialized. Drop the backend file for this local-only plan test:
>
> ```bash
> rm backend.tf
> terraform init -backend=false
> ```

```bash
# Still in ./out/lambda-api, with backend.tf removed
terraform plan \
  -var='api_name=BAD_NAME_WITH_UNDERSCORES_AND_CAPS' \
  -var='environment=production'
```

**Expected:** Two distinct error messages:
```
│ Error: Invalid value for variable
│   on variables.tf line N:
│   ...
│ api_name must be 3-32 chars, lowercase letters/digits/hyphens.

│ Error: Invalid value for variable
│   on variables.tf line N:
│   ...
│ environment must be one of dev, staging, prod.
```

**Pass criteria:**
- ✅ `terraform plan` exits non-zero
- ✅ Both error messages appear (not just one)
- ✅ Errors come from infra-x's validation blocks, not from AWS API calls

## Step 1.7 — Same plan with valid input should reach AWS

```bash
terraform plan \
  -var='api_name=acme-orders' \
  -var='environment=prod'
```

**Expected (without AWS creds configured):**
```
│ Error: No valid credential sources found
```

**Expected (with AWS creds):** A normal plan output showing 11 resources to create.

**Pass criteria:**
- ✅ The plan progresses past variable validation (no `Invalid value for variable` errors)
- ✅ Either it shows "Plan: 11 to add" OR fails on AWS credentials — both are acceptable. We're proving validations don't false-positive on valid input.

## Step 1.8 — Cleanup

```bash
cd ../..
rm -rf ./out/lambda-api
```

✅ **Scenario 1 complete.** You've verified: multi-file output, backend rendering, validation blocks, terraform syntax correctness, and that validations actually catch bad inputs.

---

# Scenario 2 — LLM-powered generation against local Ollama

**Goal:** Prove the planner agent talks to Ollama, parses structured JSON, and produces a stack whose variable defaults reflect the user's free-form prompt.

**Prerequisites:** One-time setup, plus Ollama installed and a code model pulled.

## Step 2.1 — Install and start Ollama

```bash
# Install (if you haven't already)
brew install ollama

# Start the daemon — leave this running in a separate terminal:
ollama serve

# In your main terminal, pull a code model. ~4.7 GB download.
ollama pull qwen2.5-coder:7b

# Quick smoke test that the model responds:
ollama run qwen2.5-coder:7b "Write hello world in Python."
# Expected: a Python snippet. Hit Ctrl+D to exit the chat.
```

**Pass criteria:**
- ✅ `ollama serve` is running and listening on `localhost:11434`
- ✅ `qwen2.5-coder:7b` is in `ollama list` output
- ✅ The smoke-test prompt returns Python code

## Step 2.2 — Verify infra-x can reach Ollama

```bash
curl -s http://localhost:11434/api/tags | head -50
# Expected: JSON listing the models you have pulled, including qwen2.5-coder:7b
```

**If `curl` returns `Connection refused`:** Ollama isn't running. Go back to Step 2.1.

## Step 2.3 — LLM-powered generation: the static site

```bash
cd "/Users/dhagash/Documents/Projects/Claude Projects/Test/Terraform Gui/infra-x"
source .venv/bin/activate

rm -rf ./out/acme-llm
infra-x generate \
  --blueprint aws-s3-static-site \
  --prompt "Static marketing site for acme-corp, production environment, deployed in eu-west-2 (London)." \
  --name acme-marketing-prod \
  --backend "s3://acme-tfstate/sites/acme-marketing.tfstate?region=eu-west-2&lock=tf-locks" \
  --out ./out/acme-llm
```

**Expected output panel (after a 10-30s spinner while the model thinks):**
```
✓ Generated acme-marketing-prod (aws, 7 resources)
Backend: s3
Notes: <one-paragraph rationale from the LLM>
```

**Pass criteria:**
- ✅ Command completes without error
- ✅ The `Notes:` field contains a non-empty rationale (this proves JSON-mode parsing worked)
- ✅ Files written to `./out/acme-llm/`

## Step 2.4 — Verify the LLM customized the variables

This is the key step. The blueprint defaults are `environment=prod`, `aws_region=us-east-1`. We told the model to use **eu-west-2**. The LLM should have changed that default.

```bash
grep -A 3 '^variable "aws_region"' ./out/acme-llm/variables.tf
```

**Expected:**
```hcl
variable "aws_region" {
  type        = string
  description = "AWS region for the bucket"
  default     = "eu-west-2"          ← changed from us-east-1
```

```bash
grep -A 3 '^variable "site_name"' ./out/acme-llm/variables.tf
```

**Expected:** A `default` derived from "acme-corp" / "acme-marketing" (something like `"acme-corp-site"` or `"acme-marketing-site"`). The exact wording depends on the model.

**Pass criteria:**
- ✅ `aws_region` default is `"eu-west-2"` (or close — `"eu-west-1"` is also acceptable; what matters is the model didn't keep the `us-east-1` default)
- ✅ `site_name` default is set (not null) and is a valid bucket name (lowercase, hyphens only)
- ✅ The new validation blocks still pass on these new defaults: run `terraform validate` to confirm

## Step 2.5 — Validate the LLM output passes Terraform

```bash
cd ./out/acme-llm
terraform init -backend=false
terraform validate
terraform plan -var="site_name=$(grep 'default     =' variables.tf | grep -i site | head -1 | sed 's/.*default     = "\(.*\)"/\1/')-test"
```

**Pass criteria:**
- ✅ `terraform validate` says "Success!"
- ✅ `terraform plan` either succeeds (with AWS creds) or fails on credentials, but **does not** fail on variable validation. If you see `Invalid value for variable`, the LLM gave you a `site_name` that doesn't match S3 naming rules — that's a finding worth filing as an issue (the LLM ignored the validation context).

## Step 2.6 — Try a deliberately ambiguous prompt

To stress-test the planner: ask for something the blueprint can't really do, and see what happens.

```bash
cd "/Users/dhagash/Documents/Projects/Claude Projects/Test/Terraform Gui/infra-x"

rm -rf ./out/eks-llm
infra-x generate \
  --blueprint aws-eks-cluster \
  --prompt "GPU-enabled cluster for ML training, large nodes, autoscaling 2-10 nodes, Kubernetes 1.30, in us-west-2." \
  --out ./out/eks-llm
```

**Expected:** The LLM should have:
- Set `aws_region = "us-west-2"` 
- Set `node_instance_type` to a GPU-capable instance (`g4dn.xlarge`, `g5.xlarge`, etc.)
- Set `node_min_size = 2` and `node_max_size = 10` (or close)
- Set `kubernetes_version = "1.30"`

```bash
grep 'default' ./out/eks-llm/variables.tf
```

**Pass criteria:**
- ✅ At least 2 of the above 4 variables have customized defaults reflecting the prompt
- ✅ The validation blocks still hold — `terraform validate` passes

**Note on quality:** A 7B model will sometimes miss subtle requirements. That's expected at v0.1 — the point of this test is to confirm the *plumbing* works. Hallucinations or partial answers should be filed as planner-improvement work for v0.2.

## Step 2.7 — Cleanup

```bash
cd "/Users/dhagash/Documents/Projects/Claude Projects/Test/Terraform Gui/infra-x"
rm -rf ./out/acme-llm ./out/eks-llm
```

✅ **Scenario 2 complete.** You've verified the LLM agent path: Ollama connection, JSON-mode response parsing, variable customization, output still valid for Terraform.

---

# Scenario 3 — Full `make verify` gate

**Goal:** Prove that the entire test suite + validation gate passes cleanly. This is what you'd run before every commit / push.

**Prerequisites:** One-time setup. `terraform` on PATH (otherwise Layer 3 auto-skips, which still counts as a pass).

## Step 3.1 — Run the full verify gate

```bash
cd "/Users/dhagash/Documents/Projects/Claude Projects/Test/Terraform Gui/infra-x"
make verify
```

**Expected output (with `terraform` installed):**
```
>> Layer 1: pytest
....................................................... 60 passed, 11 skipped

>> Layer 2: infra-x validate (blueprint + IR + renderer)
  ✓ aws-ecs-fargate-web  (18 resources)
  ✓ aws-eks-cluster  (15 resources)
  ✓ aws-lambda-api  (11 resources)
  ✓ aws-s3-static-site  (7 resources)
  ✓ gcp-cloud-run  (4 resources)
All 5 blueprint(s) OK.

>> Layer 3: terraform init+validate per blueprint
>>   aws-s3-static-site
>>   aws-lambda-api
>>   gcp-cloud-run
>>   aws-ecs-fargate-web
>>   aws-eks-cluster
>> All blueprints pass terraform validate.

>> verify OK
```

**Without `terraform`:** The same output, but Layer 3 says `Layer 3 skipped: terraform not on PATH (install for full coverage)`.

**Pass criteria:**
- ✅ Final line is `>> verify OK`
- ✅ With terraform: 60+ tests passed, 5 blueprints terraform-validated
- ✅ Without terraform: same minus Layer 3 (still a pass)

## Step 3.2 — Prove snapshot tests catch a regression

This proves the snapshot fixtures are wired correctly. We deliberately break the renderer for one second, see the snapshot test fail, then put it back.

```bash
# 1. Make a no-op-but-renderer-visible change: append a trailing comment.
python3 -c "
from pathlib import Path
p = Path('infra_x/render/hcl.py')
src = p.read_text()
# Inject a harmless extra blank line in the header; will change every output file.
patched = src.replace(
    '\"# DO NOT EDIT BY HAND if you plan to regenerate. Use \`infra-x regen\`.\\n\"',
    '\"# DO NOT EDIT BY HAND if you plan to regenerate. Use \`infra-x regen\`.\\n# (test marker)\\n\"'
)
p.write_text(patched)
"

# 2. Run only the snapshot tests — should fail loudly with a unified diff.
.venv/bin/pytest tests/test_snapshots.py -v 2>&1 | tail -20

# 3. Revert.
python3 -c "
from pathlib import Path
p = Path('infra_x/render/hcl.py')
p.write_text(p.read_text().replace(
    '\"# DO NOT EDIT BY HAND if you plan to regenerate. Use \`infra-x regen\`.\\n# (test marker)\\n\"',
    '\"# DO NOT EDIT BY HAND if you plan to regenerate. Use \`infra-x regen\`.\\n\"'
))
"

# 4. Confirm we're back to green.
.venv/bin/pytest tests/test_snapshots.py -v 2>&1 | tail -8
```

**Pass criteria:**
- ✅ Step 2 fails with a `unified_diff` showing the extra `# (test marker)` line
- ✅ Step 4 passes (5 / 5 snapshot tests green)

## Step 3.3 — Prove the new validation requirement is enforced

This proves the test in `test_terraform_validate.py::test_all_blueprints_have_at_least_one_validation` actually catches blueprints with no input validation.

```bash
# Run that specific test in isolation. Should pass.
.venv/bin/pytest tests/test_terraform_validate.py::test_all_blueprints_have_at_least_one_validation -v
```

**Expected:** `1 passed`. (If you have terraform installed, you can also run the full file: `pytest tests/test_terraform_validate.py -v` — should be 11 passed instead of 11 skipped.)

✅ **Scenario 3 complete.** Your CI gate is healthy and your snapshot tests work.

---

# Quick troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `make dev` fails on `ensurepip` | Python 3.9 (system Python) | `brew install python@3.12 && PYTHON=python3.12 make dev` |
| `infra-x: command not found` | venv not activated | `source .venv/bin/activate` |
| `Could not reach Ollama at http://localhost:11434` | Ollama not running | Start `ollama serve` in another terminal |
| `model 'qwen2.5-coder:7b' not found` | Model not pulled | `ollama pull qwen2.5-coder:7b` |
| LLM returns truncated / invalid JSON | Small model + complex prompt | Try simpler prompt, or `--model qwen2.5-coder:14b` if you have RAM |
| `terraform init` errors with `Failed to query available provider packages` | No internet, or proxy | Check connectivity; Terraform Registry must be reachable |
| `terraform plan` errors with `No valid credential sources found` | No AWS creds set | `aws configure` (only needed if you intend to apply) |
| `validation {` blocks render but plan doesn't reject bad input | Pre-1.2 Terraform | Upgrade to Terraform >= 1.2 |
| Snapshot test fails on a real change | Intended change | `make update-snapshots` and review the diff before committing |

---

# What this proves, end-to-end

Running all three scenarios green means:

| Layer | Verified by |
|---|---|
| Pydantic IR validates structure | Scenario 1 (renderer) + 60 unit tests |
| Multi-file split by category | Scenario 1.2 (file listing) |
| Backend block renders correctly | Scenario 1.3 |
| Variable validations render correctly | Scenario 1.4 |
| Validations enforced by Terraform | Scenario 1.6 |
| Generated HCL is syntactically valid | Scenario 1.5, 3.1 (Layer 3) |
| Ollama provider connects + parses JSON | Scenario 2.3 |
| Planner customizes variables from prompt | Scenario 2.4, 2.6 |
| LLM output still passes Terraform | Scenario 2.5 |
| Snapshot tests catch regressions | Scenario 3.2 |
| All blueprints have at least one validation | Scenario 3.3 |
| Whole gate runnable as one command | Scenario 3.1 |

If all three pass, the v0.1 polish bundle is a real, working product.
