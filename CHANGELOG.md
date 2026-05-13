# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-05-12

Initial public release.

### Added

- CLI (`infra-x`) with `generate`, `list-blueprints`, `show`, `validate`, and `version` commands
- Typed graph IR (`Stack`, `Service`, `Connection`, `Variable`, `Output`, `BackendConfig`) built on Pydantic v2
- HCL renderer with multi-file output organized by category (`networking.tf`, `security.tf`, `iam.tf`, `compute.tf`, `storage.tf`, `database.tf`, `cdn.tf`, `observability.tf`, `dns.tf`, `main.tf`)
- Five starter blueprints:
  - `aws-s3-static-site` — S3 + CloudFront static site with HTTPS
  - `aws-lambda-api` — Lambda + API Gateway + DynamoDB serverless API
  - `aws-ecs-fargate-web` — Fargate web app behind an ALB
  - `gcp-cloud-run` — Cloud Run service with Artifact Registry
  - `aws-eks-cluster` — Minimal EKS cluster with managed node group
- Variable validation rules baked into every blueprint
- Remote backend support via shorthand: `s3://`, `gcs://`, `tfc://`, `local`
- LLM provider abstraction with three built-in providers:
  - Ollama (local, default)
  - Anthropic (Claude models, `claude-sonnet-4-6` default)
  - OpenAI (`gpt-4o-mini` default)
- Three-layer test suite: unit tests, snapshot tests, optional `terraform validate` layer
- `make verify` gate that runs all three layers
- PyPI packaging (sdist + wheel) with bundled blueprint catalog
- GitHub Actions CI matrix (Python 3.10 / 3.11 / 3.12)
- Contributor onboarding: CONTRIBUTING.md, BLUEPRINT_AUTHORING.md, issue templates, PR template
- End-to-end test plan (E2E_TEST_PLAN.md) with three manual verification scenarios

### Architecture decisions

- Blueprint-driven, not freehand LLM. The structural Terraform comes from human-curated YAML; the LLM only fills in input values. This is what lets every blueprint pass `terraform validate`.
- IR-first design. The same typed `Stack` will drive the HCL renderer today and the visual canvas in v0.3.
- No telemetry, no proxy. Hosted LLM keys are user-supplied; we never see prompts or generated code.

[Unreleased]: https://github.com/infra-x/infra-x/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/infra-x/infra-x/releases/tag/v0.1.0
