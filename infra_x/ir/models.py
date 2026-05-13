"""
Typed graph IR for infra-x.

Everything the agent produces lives here. Today the IR drives the HCL renderer;
tomorrow the same IR will drive a React Flow canvas, an architecture diagram, and
a cost report. The whole point of having an IR is that adding a new surface is
just a new visitor over the same data.

Design notes
------------
- A Stack is a *root module*. One stack -> one set of `.tf` files.
- A Service is one Terraform resource (or, occasionally, a logical grouping that
  expands to several). Every Service has a stable `id` (Terraform local name)
  and a `type` (Terraform resource type, e.g. `aws_lambda_function`).
- A Connection is a *logical* edge between services for visualization and
  dependency reasoning; it does NOT translate to HCL by itself. Real Terraform
  references happen via `${...}` interpolations inside `Service.config`.
- `config` values are kept loose (Any) on purpose. HCL is JSON-shaped at heart,
  and forcing every resource into a strict schema would explode the v0 scope.
  Validation per resource type can be added later as opt-in plugins.
- Each Service belongs to a `category` (networking / security / compute / iam /
  storage / database / cdn / observability / other). The renderer emits one
  `<category>.tf` file per category — much friendlier for review than a single
  giant main.tf. Anything left as the default `other` lands in `main.tf`.
- A Stack may carry a `BackendConfig`. When set, the renderer emits a
  `backend.tf` with a `terraform { backend "X" { ... } }` block. Backend config
  is deterministic and never goes through the LLM.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Terraform identifier rules: letters, digits, underscores, hyphens; must start
# with a letter or underscore. We're slightly stricter (no leading digit, no
# leading hyphen) for stable codegen.
_TF_ID = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


# --- Variable validation ----------------------------------------------------


class VariableValidation(BaseModel):
    """A `validation { condition = ..., error_message = ... }` block."""

    condition: str  # raw HCL expression, e.g. 'can(regex("^[a-z]+$", var.foo))'
    error_message: str


class Variable(BaseModel):
    """A Terraform input variable (`variable "x" { ... }`)."""

    name: str
    type: str = "string"  # string | number | bool | list(...) | map(...) | object(...)
    description: str | None = None
    default: Any | None = None
    sensitive: bool = False
    validations: list[VariableValidation] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not _TF_ID.match(v):
            raise ValueError(f"invalid Terraform identifier: {v!r}")
        return v


class Output(BaseModel):
    """A Terraform output (`output "x" { value = ... }`)."""

    name: str
    value: str  # raw HCL expression, e.g. "aws_s3_bucket.site.bucket_domain_name"
    description: str | None = None
    sensitive: bool = False

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not _TF_ID.match(v):
            raise ValueError(f"invalid Terraform identifier: {v!r}")
        return v


# --- Categories -------------------------------------------------------------

# Categories drive multi-file output. They're a *fixed* enum on purpose: a
# small, opinionated taxonomy is what makes generated code feel idiomatic.
# If you need a new bucket, add it here intentionally rather than letting
# blueprint authors invent ad-hoc category names.
ServiceCategory = Literal[
    "networking",      # VPC, subnets, route tables, IGW, NAT, peering
    "security",        # security groups, NACLs, WAF, KMS keys, secrets
    "iam",             # roles, policies, service accounts, OIDC providers
    "compute",         # ECS, EC2, Lambda, Cloud Run, GKE, ASGs
    "storage",         # S3, GCS, EFS, EBS volumes
    "database",        # RDS, DynamoDB, Cloud SQL, ElastiCache
    "cdn",             # CloudFront, Cloud CDN
    "observability",   # CloudWatch log groups, alarms, dashboards
    "dns",             # Route53, Cloud DNS
    "other",           # ungrouped — falls into main.tf
]


ServiceKind = Literal["resource", "data"]


class Service(BaseModel):
    """One Terraform `resource` or `data` block in the stack."""

    id: str = Field(..., description="Local name in HCL (e.g. 'site_bucket')")
    type: str = Field(..., description="Terraform resource type (e.g. 'aws_s3_bucket')")
    kind: ServiceKind = Field(
        default="resource",
        description="`resource` (default) emits `resource \"T\" \"ID\"`; `data` emits `data \"T\" \"ID\"`.",
    )
    config: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, description="Other Service ids")
    category: ServiceCategory = "other"

    # Optional UI hints — ignored by HCL renderer, used by the future canvas.
    display_name: str | None = None
    icon: str | None = None
    position: tuple[float, float] | None = None

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _TF_ID.match(v):
            raise ValueError(f"invalid service id {v!r} (must match {_TF_ID.pattern})")
        return v

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        # e.g. aws_s3_bucket, google_cloud_run_v2_service
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(f"invalid resource type {v!r}")
        return v


ConnectionKind = Literal[
    "invokes",      # A calls B (e.g. Lambda -> DynamoDB)
    "reads",        # A reads from B
    "writes",       # A writes to B
    "stores_state", # A stores its state in B
    "serves",       # A serves traffic from B (e.g. CloudFront -> S3)
    "depends_on",   # generic
]


class Connection(BaseModel):
    """Logical edge for visualization & dependency reasoning."""

    from_id: str = Field(..., alias="from")
    to_id: str = Field(..., alias="to")
    kind: ConnectionKind = "depends_on"
    label: str | None = None

    model_config = {"populate_by_name": True}


# --- Backend configuration --------------------------------------------------


class S3Backend(BaseModel):
    """`backend "s3"` — AWS S3 remote state."""

    kind: Literal["s3"] = "s3"
    bucket: str
    key: str  # e.g. "stacks/acme-site/terraform.tfstate"
    region: str = "us-east-1"
    dynamodb_table: str | None = None  # for state locking; strongly recommended
    encrypt: bool = True

    @field_validator("bucket")
    @classmethod
    def _valid_bucket(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", v):
            raise ValueError(f"invalid S3 bucket name {v!r} (must be lowercase, 3-63 chars)")
        return v


class GCSBackend(BaseModel):
    """`backend "gcs"` — GCS remote state."""

    kind: Literal["gcs"] = "gcs"
    bucket: str
    prefix: str  # e.g. "stacks/acme-app"


class TerraformCloudBackend(BaseModel):
    """`cloud { ... }` block — Terraform Cloud / HCP Terraform."""

    kind: Literal["tfc"] = "tfc"
    organization: str
    workspace_name: str | None = None
    workspace_tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _name_or_tags(self) -> TerraformCloudBackend:
        if not self.workspace_name and not self.workspace_tags:
            raise ValueError(
                "TerraformCloudBackend requires either workspace_name or workspace_tags"
            )
        return self


class LocalBackend(BaseModel):
    """`backend "local"` — explicit opt-in to local state. Default if `backend` is None."""

    kind: Literal["local"] = "local"
    path: str | None = None  # default: terraform.tfstate in module dir


BackendConfig = S3Backend | GCSBackend | TerraformCloudBackend | LocalBackend


# --- Provider requirements --------------------------------------------------


class ProviderRequirement(BaseModel):
    """An entry in `terraform { required_providers { ... } }`.

    Use for non-default providers a stack needs in addition to its main
    cloud provider — e.g. `hashicorp/archive` for `archive_file` data blocks,
    `hashicorp/random` for `random_id`, `hashicorp/null` for `null_resource`.
    """

    local_name: str  # e.g. "archive" — what HCL refers to it as
    source: str  # e.g. "hashicorp/archive"
    version: str  # e.g. "~> 2.4"


# --- Stack ------------------------------------------------------------------


class Stack(BaseModel):
    """A whole Terraform root module."""

    name: str
    provider: Literal["aws", "gcp", "azure", "cloudflare", "multi"] = "aws"
    region: str | None = None
    description: str | None = None

    variables: list[Variable] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)
    outputs: list[Output] = Field(default_factory=list)
    backend: BackendConfig | None = Field(default=None, discriminator="kind")
    extra_providers: list[ProviderRequirement] = Field(
        default_factory=list,
        description="Additional terraform providers beyond the main cloud one (e.g. archive, random).",
    )

    # Provenance — useful for the visual canvas and for "regenerate this stack".
    blueprint_id: str | None = None
    blueprint_version: str | None = None

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9-]{0,62}$", v):
            raise ValueError(
                f"invalid stack name {v!r} — use lowercase letters, digits, hyphens"
            )
        return v

    @model_validator(mode="after")
    def _check_refs(self) -> Stack:
        ids = {s.id for s in self.services}
        for s in self.services:
            for d in s.depends_on:
                if d not in ids:
                    raise ValueError(f"service {s.id!r} depends_on unknown id {d!r}")
        for c in self.connections:
            if c.from_id not in ids:
                raise ValueError(f"connection from unknown service {c.from_id!r}")
            if c.to_id not in ids:
                raise ValueError(f"connection to unknown service {c.to_id!r}")
        return self
