"""
Backend shorthand parser.

Lets users specify Terraform remote-state backends with a compact URL-ish syntax
on the CLI instead of a verbose YAML file:

    s3://my-state-bucket/sites/acme.tfstate?region=us-east-1&lock=tf-locks
    gcs://my-state-bucket/stacks/acme-app
    tfc://acme-corp/acme-site                          # workspace_name
    tfc://acme-corp?tags=prod,site                     # workspace_tags
    local                                              # explicit local backend
    local:./state/terraform.tfstate                    # local with custom path

Anything more complicated should go through `--backend-config <yaml>` which
uses the full IR types directly.

Why a parser instead of subcommands? Because backend choice is the kind of
thing you want to set per-environment in a Makefile or a CI variable, so a
single string is the most ergonomic surface.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from infra_x.ir.models import (
    BackendConfig,
    GCSBackend,
    LocalBackend,
    S3Backend,
    TerraformCloudBackend,
)


class BackendParseError(ValueError):
    """Raised when --backend shorthand can't be parsed."""


def parse_backend_shorthand(s: str) -> BackendConfig:
    """Parse a `--backend` CLI value into a BackendConfig."""
    s = s.strip()
    if not s:
        raise BackendParseError("empty backend string")

    # Bare 'local' or 'local:<path>'
    if s == "local":
        return LocalBackend()
    if s.startswith("local:"):
        return LocalBackend(path=s[len("local:") :] or None)

    # URL forms
    parsed = urlsplit(s)
    scheme = parsed.scheme
    if scheme == "s3":
        return _parse_s3(parsed)
    if scheme == "gcs":
        return _parse_gcs(parsed)
    if scheme == "tfc":
        return _parse_tfc(parsed)

    raise BackendParseError(
        f"unknown backend scheme {scheme!r}. "
        "Use one of: s3://..., gcs://..., tfc://..., local, local:<path>"
    )


# --- helpers ---------------------------------------------------------------


def _parse_s3(p) -> S3Backend:
    bucket = p.netloc
    if not bucket:
        raise BackendParseError("s3 backend needs a bucket: s3://BUCKET/key")
    key = p.path.lstrip("/")
    if not key:
        raise BackendParseError("s3 backend needs a key path: s3://bucket/PATH/TO/state.tfstate")
    if not key.endswith(".tfstate"):
        # Accept both with and without; default to .tfstate suffix for sanity.
        key = key + "/terraform.tfstate" if not key.endswith("/") else key + "terraform.tfstate"
    qs = {k: v[0] for k, v in parse_qs(p.query).items()}
    return S3Backend(
        bucket=bucket,
        key=key,
        region=qs.get("region", "us-east-1"),
        dynamodb_table=qs.get("lock") or qs.get("dynamodb_table"),
        encrypt=qs.get("encrypt", "true").lower() != "false",
    )


def _parse_gcs(p) -> GCSBackend:
    bucket = p.netloc
    if not bucket:
        raise BackendParseError("gcs backend needs a bucket: gcs://BUCKET/prefix")
    prefix = p.path.lstrip("/")
    if not prefix:
        raise BackendParseError("gcs backend needs a prefix: gcs://bucket/PREFIX")
    return GCSBackend(bucket=bucket, prefix=prefix)


def _parse_tfc(p) -> TerraformCloudBackend:
    org = p.netloc
    if not org:
        raise BackendParseError("tfc backend needs an organization: tfc://ORG/workspace")
    workspace = p.path.lstrip("/")
    qs = {k: v for k, v in parse_qs(p.query).items()}
    if workspace:
        return TerraformCloudBackend(organization=org, workspace_name=workspace)
    if "tags" in qs:
        tags = [t.strip() for t in qs["tags"][0].split(",") if t.strip()]
        return TerraformCloudBackend(organization=org, workspace_tags=tags)
    raise BackendParseError(
        "tfc backend needs either a workspace name (tfc://org/workspace) "
        "or tags (tfc://org?tags=prod,site)"
    )
