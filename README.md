# infra-x

> Open-source AI Terraform generator with versioned blueprints. Bring your own LLM (Ollama, Anthropic, OpenAI). Local-first. No telemetry.

`infra-x` turns natural-language requirements into production-shaped Terraform. Unlike "ask ChatGPT to write Terraform" approaches, every output flows through a versioned **blueprint** (a peer-reviewed YAML recipe) and a typed **graph IR**, so the LLM can't hallucinate a resource type that doesn't exist or stitch services together in nonsense ways. The same IR will drive a visual canvas in a future release.

## Why infra-x

**Blueprints, not vibes.** Each blueprint is a recipe with inputs, services, connections, and validations. The LLM customizes — it does not invent. Every blueprint passes `terraform validate` out of the box, and we have a CI layer that proves it.

**Local-first.** The default LLM is your own Ollama install. Your prompts, your code, your infrastructure topology — none of it leaves your machine.

**Bring your own key.** When you do want a hosted model, you supply the key. We don't proxy, we don't bill you, we don't retain prompts.

**One IR, many surfaces.** The typed `Stack` IR drives the HCL renderer today. Tomorrow it drives the visual canvas, the cost report, and the architecture diagram. The data model is the contract.

**No telemetry. No accounts. No SaaS bill.** Period.

## Quick start

### Install

Once published to PyPI:

```bash
pipx install infra-x          # recommended (isolated install)
# or
pip install infra-x
```

From source (today):

```bash
git clone https://github.com/infra-x/infra-x.git
cd infra-x
make dev
source .venv/bin/activate
```

### Pick an LLM

**Local (default, free, private):** install Ollama and pull a code model.

```bash
brew install ollama        # macOS; see ollama.com for Linux/Windows
ollama serve &
ollama pull qwen2.5-coder:7b   # ~4.7GB on disk; comfortable in 16GB RAM
```

**Hosted (faster, costs money):**

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # https://console.anthropic.com
# or
export OPENAI_API_KEY=sk-...             # https://platform.openai.com/api-keys
```

### Generate

```bash
# See what's available
infra-x list-blueprints

# Deterministic mode — no LLM, just renders blueprint defaults. Great for testing.
infra-x generate -b aws-s3-static-site -o ./out/site --no-llm

# AI-customized mode (defaults to local Ollama)
infra-x generate \
  -b aws-s3-static-site \
  -p "Static marketing site for acme.com, prod environment" \
  -o ./out/acme-site

# Same, with Anthropic
infra-x generate \
  -b aws-lambda-api \
  -p "REST API for our todos service, prod" \
  --provider anthropic \
  -o ./out/todos-api

# Same, with OpenAI
infra-x generate \
  -b gcp-cloud-run \
  -p "API service for our internal tools" \
  --provider openai \
  --model gpt-4o-mini \
  -o ./out/internal-api
```

### Apply

```bash
cd out/acme-site
terraform init
terraform plan
terraform apply
```

### Add a remote backend

State management is one of the things people get wrong most often, so we made the common cases easy:

```bash
# S3 + DynamoDB locking
infra-x generate -b aws-lambda-api -o ./out/api --no-llm \
  --backend "s3://my-tfstate-bucket/api/state.tfstate?region=us-east-1&lock=tf-locks"

# GCS
infra-x generate -b gcp-cloud-run -o ./out/api --no-llm \
  --backend "gcs://my-tfstate-bucket/api"

# Terraform Cloud / HCP Terraform
infra-x generate -b aws-eks-cluster -o ./out/cluster --no-llm \
  --backend "tfc://my-org/cluster-prod"
```

## Blueprints

| ID | Cloud | Description | Est. cost / mo |
|---|---|---|---|
| `aws-s3-static-site` | AWS | S3 + CloudFront static site with HTTPS | $1–10 |
| `aws-lambda-api` | AWS | Lambda + API Gateway + DynamoDB | $5–25 |
| `aws-ecs-fargate-web` | AWS | Fargate web app behind an ALB | $30–80 |
| `gcp-cloud-run` | GCP | Cloud Run service with Artifact Registry | $10–30 |
| `aws-eks-cluster` | AWS | Minimal EKS cluster with managed node group | $80+ |

Each blueprint ships with sane defaults, input validation rules, multi-file output organized by category (`networking.tf`, `security.tf`, `iam.tf`, etc.), and any companion files needed to make the stack work out of the box (e.g. a placeholder Lambda handler).

## How it compares

**vs. infra.new and other SaaS Terraform generators.** They're closed-source and hosted; we're open-source and local. Your code never leaves your laptop unless you opt into a hosted LLM.

**vs. aiac (Firefly) and prompt-to-code tools.** aiac and similar tools ask the LLM to generate Terraform from scratch. That works for snippets ("give me an S3 bucket") but breaks down on full stacks: the LLM hallucinates resource types, gets argument names wrong, invents fields. infra-x locks the structural shape down with curated blueprints and only lets the LLM fill in input values. Result: every blueprint passes `terraform validate`.

**vs. Brainboard.** Brainboard is a polished closed-source SaaS visual designer. We're an open-source CLI today; the visual editor is on the roadmap and the IR is built to support it. If you need a visual designer right now and you're okay with SaaS, use Brainboard. If you want something open and local, use us.

**vs. Cloudcraft.** Cloudcraft is a diagramming tool for *existing* infrastructure (you connect your AWS account, it draws the diagram). Different problem. Use Cloudcraft to document; use infra-x to build.

## Architecture

```
prompt + blueprint
        │
        ▼
┌──────────────────┐      ┌─────────────────────────┐
│   Planner agent  │─────▶│      LLM provider       │
│  (prompt → IR)   │      │  Ollama / Anthropic /   │
└──────────────────┘      │        OpenAI           │
        │                 └─────────────────────────┘
        ▼
┌──────────────────┐
│  Stack IR (typed,│  ◀── future: render to canvas, diagram, cost report
│   Pydantic)      │
└──────────────────┘
        │
        ▼
┌──────────────────┐
│  HCL renderer    │ ───▶  *.tf files (split by category)
└──────────────────┘
```

The key idea: **the LLM is on a short leash.** It picks values for the blueprint's input slots (service name, environment, region, sizing) but it does not invent resources. The structural Terraform — every resource type, every connection, every IAM permission — comes from the human-curated blueprint. This is what lets us promise that generated stacks parse, validate, and follow best practices.

## Project layout

```
infra-x/
├── infra_x/
│   ├── cli.py                  # Typer CLI entry point
│   ├── ir/                     # Pydantic graph IR (Stack, Service, Connection, ...)
│   ├── blueprints/             # YAML blueprint loader + bundled catalog
│   │   └── catalog/            #   one file per blueprint
│   ├── llm/                    # Provider interface + Ollama / Anthropic / OpenAI clients
│   ├── agent/                  # Planner: prompt + blueprint -> IR
│   ├── render/                 # IR -> HCL (multi-file, category-split)
│   └── backend.py              # Shorthand parser for s3:// / gcs:// / tfc:// backends
├── tests/                      # unit + snapshot + terraform-validate layers
├── E2E_TEST_PLAN.md            # step-by-step manual verification scenarios
├── Makefile                    # dev / verify / build / publish / release-check
└── pyproject.toml
```

## Roadmap

**v0.1 (today)** — CLI, 5 blueprints, multi-file output, variable validations, remote backends, three LLM providers, three layers of tests.

**v0.2 (next)** — Anthropic and OpenAI hardening, JSON Schema export of the `Stack` IR, more blueprints (Postgres on RDS, SQS+Lambda async, GitHub Actions deploy pipeline with OIDC), `terraform fmt` post-processing.

**v0.3** — Visual editor MVP (React + React Flow). The same `Stack` IR renders to HCL today; v0.3 adds a canvas surface so you can drag services around, connect them, and round-trip back to Terraform.

**v0.4+** — Landing-zone-shaped blueprints for organizations (multi-account AWS Organizations + shared networking baseline). Module-aware generation against private registries. Cost estimation surface.

## Development

```bash
make dev               # create venv, install in editable mode
make test              # pytest only
make verify            # pytest + blueprint validation + terraform validate (if installed)
make update-snapshots  # refresh snapshot fixtures after intentional renderer changes
make build             # produce sdist + wheel under ./dist/
make release-check     # smoke-test the wheel in a throwaway venv
make publish-test      # upload to TestPyPI
make publish           # upload to PyPI for real
```

The test suite has three layers, each catching different classes of bug:

1. **Unit tests** (`pytest`) — IR validation, renderer correctness, backend parsing, LLM provider request shapes.
2. **Snapshot tests** — every blueprint's rendered output is pinned to a fixture. Renderer changes that affect output fail loudly with a diff. Refresh with `make update-snapshots` once you've reviewed the change.
3. **`terraform validate` layer** — actually invokes Terraform against every blueprint's output. Skipped if Terraform isn't installed; required in CI.

## Contributing

Open an issue or PR — we're early enough that almost anything is on the table. Particularly welcome: new blueprints (RDS, SQS, GitHub Actions deploy, Cloud SQL, more GCP), additional LLM providers (Bedrock, Gemini), bug reports against the renderer.

## License

MIT. See [LICENSE](LICENSE).
