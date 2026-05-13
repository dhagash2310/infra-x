# Blueprint Authoring Guide

A blueprint is a YAML file under `infra_x/blueprints/catalog/` that describes one production-shaped Terraform stack. This guide walks you through writing one from scratch, testing it locally, and submitting it.

You don't need to write any Python — the YAML loader, the planner, and the HCL renderer are all generic. Your job is to declare what resources should exist, how they connect, what inputs the user can tune, and what outputs they get back.

## The 30-second mental model

Every blueprint declares four things:

1. **Variables** — what the user can configure (`region`, `environment`, sizing knobs).
2. **Services** — every Terraform resource the stack needs, with values that often reference variables.
3. **Connections** — logical edges between services, used today for dependency reasoning and tomorrow for the visual canvas.
4. **Outputs** — what the user gets back after `terraform apply` (URLs, ARNs, IDs).

The CLI loads your YAML, the planner asks the LLM to fill in variable values from the user's prompt (or skips that with `--no-llm`), and the renderer walks the result and writes `.tf` files.

## File layout

Each blueprint is one self-contained file:

```
infra_x/blueprints/catalog/
├── aws-s3-static-site.yaml
├── aws-lambda-api.yaml
├── aws-ecs-fargate-web.yaml
├── gcp-cloud-run.yaml
├── aws-eks-cluster.yaml
└── your-new-blueprint.yaml
```

Naming convention: `<cloud>-<thing>.yaml`. Use lowercase, hyphens, no underscores, no version suffixes.

## Top-level schema

```yaml
id: aws-rds-postgres                 # required, must match filename
name: AWS RDS Postgres (small)       # required, human-friendly
description: Single-AZ Postgres on RDS for dev/staging workloads.
provider: aws                        # aws | gcp | azure | cloudflare
region: us-east-1                    # default region for the provider block
version: 0.1.0                       # blueprint version (independent of infra-x)
estimated_cost_usd_monthly: [25, 60] # [low, high] — shown in `infra-x list-blueprints`
estimated_setup_minutes: [5, 10]
tags: [database, postgres, aws, rds]

inputs:
  # User-facing knobs the LLM is allowed to fill in from the prompt.
  - name: db_name
    type: string
    description: Database name (will be lowercased and prefixed with environment)
    required: true
    examples: [orders, users, analytics]
  - name: environment
    type: string
    default: dev

variables:
  # Terraform variables. These end up in variables.tf and become `var.X`
  # references throughout the rest of the blueprint.
  - name: db_name
    type: string
    validations:
      - condition: 'can(regex("^[a-z][a-z0-9_]{2,62}$", var.db_name))'
        error_message: db_name must be 3-63 chars, lowercase, start with a letter.
  - name: environment
    type: string
    default: dev
    validations:
      - condition: 'contains(["dev", "staging", "prod"], var.environment)'
        error_message: environment must be one of dev, staging, prod.

services:
  # The actual Terraform resources. See "Service blocks" below.
  - id: db
    type: aws_db_instance
    category: database
    config: { ... }

connections:
  # Logical edges. See "Connections" below.
  - from: api
    to: db
    kind: writes

outputs:
  - name: db_endpoint
    value: aws_db_instance.db.endpoint
    description: Connect to the DB at this hostname:port

extra_providers:
  # Optional: declare providers beyond the main cloud one.
  - local_name: random
    source: hashicorp/random
    version: "~> 3.6"

companion_files:
  # Optional: ship non-Terraform files alongside the .tf output (placeholder
  # source code, sample scripts, etc.). Key is the relative path from the
  # output directory; value is the file content.
  scripts/seed.sql: |
    CREATE TABLE example (id SERIAL PRIMARY KEY);

agent_guidance: |
  Notes for the LLM when this blueprint is selected. What the user might want
  to customize, what NOT to change, what to suggest if they ask for X.
```

## Service blocks

A service is one Terraform resource. The most common shape:

```yaml
- id: api_table
  type: aws_dynamodb_table
  category: database
  display_name: DynamoDB table   # optional — used by future visual canvas
  icon: dynamodb                 # optional — used by future visual canvas
  config:
    name: ${var.api_name}-${var.environment}
    billing_mode: PAY_PER_REQUEST
    hash_key: pk
```

Three things to know:

**`id`** is the local name in HCL. It becomes `resource "aws_dynamodb_table" "api_table"` and is referenced elsewhere as `aws_dynamodb_table.api_table.arn`. Use `snake_case`. Must be unique within the blueprint.

**`type`** is the literal Terraform resource type. Use the exact string from the provider docs.

**`category`** drives multi-file output organization. Pick from this fixed list — there's no "other" workaround unless you really mean it:

- `networking` — VPC, subnets, route tables, IGW, NAT, peering
- `security` — security groups, NACLs, WAF, KMS keys, secrets
- `iam` — roles, policies, service accounts, OIDC providers
- `compute` — ECS, EC2, Lambda, Cloud Run, GKE, ASGs
- `storage` — S3, GCS, EFS, EBS volumes
- `database` — RDS, DynamoDB, Cloud SQL, ElastiCache
- `cdn` — CloudFront, Cloud CDN
- `observability` — CloudWatch log groups, alarms, dashboards
- `dns` — Route53, Cloud DNS
- `other` — only for truly cross-cutting things; lands in `main.tf`

If your blueprint needs a category that isn't here, open an issue — adding one is a deliberate decision, not a per-blueprint thing.

### Referencing variables and other resources

Use Terraform's `${...}` syntax inside string values. The renderer is smart enough to know when to strip the `${}` wrapper (single expression) and when to keep it as a quoted template (mixed with literal text):

```yaml
config:
  name: ${var.api_name}-${var.environment}                 # template — quoted
  role: ${aws_iam_role.lambda_role.arn}                    # single expr — unwrapped
  function_name: my-${var.environment}-fn                  # template — quoted
```

This is the most common mistake new authors make. When in doubt, run `make verify` — it catches both forms by actually invoking `terraform validate`.

### Nested blocks

Terraform has two kinds of nested constructs and the YAML format distinguishes them:

**`_block`** (singular) — one nested block:

```yaml
config:
  point_in_time_recovery:
    _block:
      enabled: true
```

renders to:

```hcl
point_in_time_recovery {
  enabled = true
}
```

**`_blocks`** (plural) — repeated nested blocks:

```yaml
config:
  attribute:
    _blocks:
      - name: pk
        type: S
      - name: sk
        type: S
```

renders to:

```hcl
attribute {
  name = "pk"
  type = "S"
}

attribute {
  name = "sk"
  type = "S"
}
```

If you forget the `_block` / `_blocks` wrapper, the renderer will treat the value as a regular argument map (`x = { ... }`), which is usually wrong for Terraform's syntax. Check the provider docs to know which form a given argument needs.

### Map keys with dots or slashes

Some real-world tags need keys like `kubernetes.io/role/elb`. The renderer auto-quotes any key that isn't a valid HCL bare identifier, so you can write them naturally — just remember to quote the YAML key so YAML doesn't get confused:

```yaml
tags:
  Name: ${var.cluster_name}-subnet
  "kubernetes.io/role/elb": "1"
```

### Data blocks

For `data "X" "Y"` blocks (read-only, not managed resources), set `kind: data`:

```yaml
- id: api_handler_zip
  type: archive_file
  kind: data
  category: compute
  config:
    type: zip
    source_dir: ${path.module}/lambda_src
    output_path: ${path.module}/lambda_src.zip
```

This emits `data "archive_file" "api_handler_zip" { ... }` instead of `resource ...`. Reference it from other services as `data.archive_file.api_handler_zip.output_path`.

### Multi-line strings (heredocs, JSON policies)

Use YAML's `|-` block scalar for any value that contains newlines or quotes:

```yaml
config:
  assume_role_policy: |
    {
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": { "Service": "lambda.amazonaws.com" },
        "Action": "sts:AssumeRole"
      }]
    }
```

The renderer emits any value containing `\n` as an HCL heredoc (`<<-EOT ... EOT`).

## Variables and validations

Every input the LLM is allowed to fill in needs a corresponding `variables:` entry, otherwise it has nowhere to land in the generated Terraform. Always add validation rules — they catch typos and cloud-provider naming-rule violations at `terraform plan` time, before anything tries to actually deploy:

```yaml
variables:
  - name: bucket_name
    type: string
    validations:
      - condition: 'can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))'
        error_message: bucket_name must be 3-63 chars, lowercase, no underscores (S3 rules).
```

**Important constraint:** Terraform <1.9 forbids cross-variable references inside a `validation` block. So `condition: 'var.min_size <= var.max_size'` will fail. Keep each validation self-contained (only references its own variable) and rely on the cloud provider to reject impossible combinations at apply-time.

## Connections

Connections are logical edges between services. They don't translate to HCL by themselves — actual Terraform references happen via `${...}` interpolations in service configs. Connections exist for two reasons: dependency reasoning, and the future visual canvas.

```yaml
connections:
  - from: api_gateway
    to: api_handler
    kind: invokes
  - from: api_handler
    to: api_table
    kind: reads
  - from: api_handler
    to: api_table
    kind: writes
```

Valid `kind` values: `invokes`, `reads`, `writes`, `stores_state`, `serves`, `depends_on`.

## extra_providers and companion_files

If your blueprint uses Terraform providers beyond the main cloud one, declare them:

```yaml
extra_providers:
  - local_name: archive
    source: hashicorp/archive
    version: "~> 2.4"
```

These get added to `versions.tf` automatically.

If your blueprint references files that need to ship alongside the `.tf` output (placeholder source code, sample scripts), use `companion_files`. The `aws-lambda-api` blueprint uses this to ship a placeholder `lambda_src/index.js` so `terraform plan` works out of the box:

```yaml
companion_files:
  lambda_src/index.js: |
    exports.handler = async (event) => {
      return { statusCode: 200, body: JSON.stringify({ message: "Hello!" }) };
    };
```

## agent_guidance

A short prose note that's shown to the LLM when this blueprint is selected. Use it to steer customization decisions:

```yaml
agent_guidance: |
  Customizations to consider:
  - If the user mentions Python or Go, change `runtime` accordingly.
  - If the user wants a custom domain, add `aws_apigatewayv2_domain_name` etc.
  - Do NOT remove the X-Ray tracing block.
```

This is not magic — it's literally inserted into the LLM's system prompt. Be specific.

## Testing your blueprint locally

Three layers, in increasing order of confidence:

**1. Loader and renderer round-trip.** The fastest check — just makes sure the YAML parses and the renderer doesn't crash:

```bash
infra-x validate your-new-blueprint
```

**2. Generate and inspect.** Render the deterministic version and read what came out:

```bash
infra-x generate -b your-new-blueprint --no-llm -o /tmp/check
ls /tmp/check/
cat /tmp/check/main.tf
```

**3. Terraform validate.** The real test — does the rendered output actually parse as Terraform?

```bash
cd /tmp/check
terraform init -backend=false
terraform validate
```

If all three pass, your blueprint is in good shape.

## Adding it to the test suite

Two files need to know about your blueprint:

**`tests/test_blueprints.py`** and **`tests/test_snapshots.py`** — both have an `ALL_BLUEPRINTS` list at the top. Add your blueprint ID to both.

**Snapshot fixtures** — generate them once with:

```bash
make update-snapshots
```

Then commit the new files under `tests/snapshots/<your-blueprint-id>/`. Future renderer changes that affect your blueprint will fail loudly until someone reviews and re-runs `make update-snapshots`.

**`tests/test_terraform_validate.py`** has an `ALL_BLUEPRINTS` list too — add your blueprint there to enforce real Terraform validation in CI.

## Submitting your PR

1. Fork the repo and create a branch named after your blueprint (`add-aws-rds-postgres`).
2. Add your YAML under `infra_x/blueprints/catalog/`.
3. Update the three `ALL_BLUEPRINTS` lists.
4. Run `make verify` — all three test layers should pass.
5. Commit your snapshot fixtures.
6. Update the blueprint table in the README.
7. Open a PR with a one-paragraph description: what the blueprint deploys, who it's for, anything reviewers should know.

We try to review blueprint PRs within a few days. Common review feedback: "this resource shouldn't be in `iam`, it's `security`," "missing input validation on this field," "the agent_guidance should mention X."

## Common pitfalls

**Blueprint ID doesn't match filename.** The loader looks them up by filename; the `id:` field has to match exactly.

**Forgot `_block` / `_blocks`.** The renderer will emit a regular `arg = { ... }` and Terraform will fail to parse. Check provider docs.

**Used `${...}` for a value that's already an expression.** If you write `value: ${aws_s3_bucket.b.arn}` in an `outputs:` entry, the renderer correctly strips the wrapper. If you write `value: aws_s3_bucket.b.arn` (no `${...}`), it also works — both are accepted. But if you write `value: ${var.x}-${var.y}`, the renderer detects the literal `-` and quotes the whole thing.

**Cross-variable validation.** `condition: 'var.min <= var.max'` fails on Terraform <1.9. Keep each validation self-referential.

**Hardcoded region.** Use `var.aws_region` (or equivalent) in resource configs, not the literal string. Otherwise users can't switch regions.

## Questions?

Open a GitHub Discussion before sinking time into a complex blueprint. We'd rather help you scope it right up front than ask for big changes at PR review.
