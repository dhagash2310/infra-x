"""Tests for the HCL renderer."""

from __future__ import annotations

from infra_x.ir.models import (
    GCSBackend,
    LocalBackend,
    Output,
    ProviderRequirement,
    S3Backend,
    Service,
    Stack,
    TerraformCloudBackend,
    Variable,
    VariableValidation,
)
from infra_x.render.hcl import HCLRenderer, _render_value


def test_render_primitive_values():
    assert _render_value("hello") == '"hello"'
    assert _render_value(True) == "true"
    assert _render_value(False) == "false"
    assert _render_value(None) == "null"
    assert _render_value(42) == "42"
    assert _render_value(3.14) == "3.14"


def test_render_raw_expression_strips_dollar_braces():
    """`${var.foo}` should render as the bare HCL expression `var.foo`."""
    assert _render_value("${var.foo}") == "var.foo"
    assert _render_value("${aws_s3_bucket.site.id}") == "aws_s3_bucket.site.id"


def test_render_string_with_interpolation_stays_quoted():
    """A string that *contains* but isn't *only* `${...}` stays a string literal."""
    out = _render_value("hello-${var.name}")
    assert out.startswith('"') and out.endswith('"')
    assert "${var.name}" in out


def test_render_chained_interpolations_stays_quoted():
    """REGRESSION: `${a}-${b}` was getting greedily stripped to `a}-${b`."""
    out = _render_value("${var.api_name}-${var.environment}")
    # Must be quoted as a string literal, NOT emitted as a bare expression.
    assert out == '"${var.api_name}-${var.environment}"', f"got: {out!r}"


def test_render_three_chained_interpolations():
    out = _render_value("${var.a}/${var.b}/${var.c}")
    assert out == '"${var.a}/${var.b}/${var.c}"'


def test_render_interpolation_with_nested_braces_stays_raw():
    """`${jsonencode({k = "v"})}` is a single raw expression — depth counting must allow nested braces."""
    out = _render_value('${jsonencode({k = "v"})}')
    assert out == 'jsonencode({k = "v"})', f"got: {out!r}"


def test_render_interpolation_followed_by_text_stays_quoted():
    """`${var.x}suffix` is a string, not a raw expression."""
    out = _render_value("${var.x}suffix")
    assert out == '"${var.x}suffix"'


def test_render_short_list_is_inline():
    assert _render_value(["a", "b"]) == '["a", "b"]'


def test_render_simple_resource():
    s = Stack(
        name="t",
        services=[
            Service(
                id="bucket",
                type="aws_s3_bucket",
                category="storage",
                config={"bucket": "my-bucket", "force_destroy": True},
            )
        ],
    )
    files = HCLRenderer(s).files()
    storage_tf = files["storage.tf"]
    assert 'resource "aws_s3_bucket" "bucket"' in storage_tf
    assert 'bucket        = "my-bucket"' in storage_tf
    assert "force_destroy = true" in storage_tf


def test_render_nested_block():
    s = Stack(
        name="t",
        services=[
            Service(
                id="x",
                type="aws_lb",
                category="networking",
                config={
                    "name": "x",
                    "access_logs": {"_block": {"bucket": "logs", "enabled": True}},
                },
            )
        ],
    )
    networking_tf = HCLRenderer(s).files()["networking.tf"]
    assert "access_logs {" in networking_tf
    assert 'bucket  = "logs"' in networking_tf
    assert "enabled = true" in networking_tf


def test_render_repeated_blocks():
    s = Stack(
        name="t",
        services=[
            Service(
                id="t1",
                type="aws_dynamodb_table",
                category="database",
                config={
                    "name": "t1",
                    "attribute": {
                        "_blocks": [
                            {"name": "pk", "type": "S"},
                            {"name": "sk", "type": "S"},
                        ]
                    },
                },
            )
        ],
    )
    db_tf = HCLRenderer(s).files()["database.tf"]
    assert db_tf.count("attribute {") == 2


def test_render_includes_provider_and_versions_files():
    s = Stack(name="t", region="us-west-2")
    files = HCLRenderer(s).files()
    assert "provider.tf" in files
    assert "versions.tf" in files
    assert "us-west-2" in files["provider.tf"]
    assert 'source  = "hashicorp/aws"' in files["versions.tf"]


def test_render_variables_and_outputs():
    s = Stack(
        name="t",
        variables=[Variable(name="env", type="string", default="prod", description="Env name")],
        services=[Service(id="b", type="aws_s3_bucket", category="storage", config={"bucket": "x"})],
        outputs=[Output(name="bucket_name", value="aws_s3_bucket.b.id", description="The bucket")],
    )
    files = HCLRenderer(s).files()
    assert 'variable "env"' in files["variables.tf"]
    assert 'default     = "prod"' in files["variables.tf"]
    assert 'output "bucket_name"' in files["outputs.tf"]
    assert "value       = aws_s3_bucket.b.id" in files["outputs.tf"]


def test_output_with_single_interpolation_is_unwrapped():
    """`${aws_s3_bucket.b.arn}` → `aws_s3_bucket.b.arn` (no quotes, no ${})."""
    s = Stack(
        name="t",
        outputs=[Output(name="arn", value="${aws_s3_bucket.b.arn}")],
    )
    out = HCLRenderer(s).files()["outputs.tf"]
    assert "value       = aws_s3_bucket.b.arn" in out
    assert "${" not in out.split("value")[1].split("\n")[0]


def test_object_keys_with_dots_or_slashes_are_quoted():
    """REGRESSION: EKS subnet tags like `kubernetes.io/role/elb` must be quoted in
    HCL because they contain non-identifier characters. Emitting them bare made
    terraform parse them as `kubernetes.io.role.elb` resource references."""
    s = Stack(
        name="t",
        services=[
            Service(
                id="subnet",
                type="aws_subnet",
                category="networking",
                config={
                    "vpc_id": "${aws_vpc.v.id}",
                    "tags": {
                        "Name": "x",
                        "kubernetes.io/role/elb": "1",
                        "kubernetes.io/cluster/foo": "shared",
                    },
                },
            )
        ],
    )
    networking_tf = HCLRenderer(s).files()["networking.tf"]
    # Bare identifiers stay bare
    assert "Name " in networking_tf or "Name=" in networking_tf or "Name\n" in networking_tf
    # Keys with dots/slashes get quoted
    assert '"kubernetes.io/role/elb"' in networking_tf
    assert '"kubernetes.io/cluster/foo"' in networking_tf


def test_output_with_template_string_is_quoted():
    """REGRESSION: `${var.region}-docker.pkg.dev/${var.proj}` was being emitted bare,
    breaking terraform parse. Templates with literal text must be quoted."""
    s = Stack(
        name="t",
        outputs=[
            Output(
                name="repo_url",
                value="${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.r.repository_id}",
            )
        ],
    )
    out = HCLRenderer(s).files()["outputs.tf"]
    # The whole template must be wrapped in double quotes.
    line = next(line for line in out.splitlines() if "value" in line)
    assert line.strip().startswith('value       = "'), f"got: {line!r}"
    assert line.strip().endswith('"'), f"got: {line!r}"
    assert "${var.region}" in line
    assert "${var.project_id}" in line


def test_render_multiline_string_uses_heredoc():
    s = Stack(
        name="t",
        services=[
            Service(
                id="r",
                type="aws_iam_role",
                category="iam",
                config={"name": "x", "assume_role_policy": "line1\nline2"},
            )
        ],
    )
    iam_tf = HCLRenderer(s).files()["iam.tf"]
    assert "<<-EOT" in iam_tf
    assert "EOT" in iam_tf


# --- Multi-file output (categories) -----------------------------------------


def test_render_splits_files_by_category():
    """A stack with services in multiple categories should emit one .tf per category."""
    s = Stack(
        name="t",
        services=[
            Service(id="vpc", type="aws_vpc", category="networking", config={"cidr_block": "10.0.0.0/16"}),
            Service(id="sg", type="aws_security_group", category="security", config={"name": "x"}),
            Service(id="role", type="aws_iam_role", category="iam", config={"name": "x", "assume_role_policy": "{}"}),
            Service(id="bucket", type="aws_s3_bucket", category="storage", config={"bucket": "x"}),
        ],
    )
    files = HCLRenderer(s).files()
    assert "networking.tf" in files
    assert "security.tf" in files
    assert "iam.tf" in files
    assert "storage.tf" in files
    assert '"vpc"' in files["networking.tf"]
    assert '"sg"' in files["security.tf"]
    assert '"role"' in files["iam.tf"]
    assert '"bucket"' in files["storage.tf"]
    # main.tf still emitted but empty of resources
    assert "main.tf" in files


def test_uncategorized_services_land_in_main_tf():
    s = Stack(
        name="t",
        services=[Service(id="x", type="aws_s3_bucket", config={"bucket": "x"})],  # category defaults to "other"
    )
    files = HCLRenderer(s).files()
    assert '"x"' in files["main.tf"]
    # No category-specific files should appear
    assert "storage.tf" not in files


def test_main_tf_always_present_even_if_empty():
    """Consumers can rely on main.tf existing for hand-edits."""
    s = Stack(
        name="t",
        services=[Service(id="x", type="aws_vpc", category="networking", config={})],
    )
    files = HCLRenderer(s).files()
    assert "main.tf" in files
    assert "networking.tf" in files


# --- Variable validation blocks --------------------------------------------


def test_render_variable_with_validation_block():
    s = Stack(
        name="t",
        variables=[
            Variable(
                name="env",
                type="string",
                description="Env",
                default="prod",
                validations=[
                    VariableValidation(
                        condition='contains(["dev", "staging", "prod"], var.env)',
                        error_message="env must be dev, staging, or prod.",
                    )
                ],
            )
        ],
    )
    var_tf = HCLRenderer(s).files()["variables.tf"]
    assert "validation {" in var_tf
    assert 'condition     = contains(["dev", "staging", "prod"], var.env)' in var_tf
    assert 'error_message = "env must be dev, staging, or prod."' in var_tf


def test_variable_with_multiple_validation_blocks():
    s = Stack(
        name="t",
        variables=[
            Variable(
                name="port",
                type="number",
                default=80,
                validations=[
                    VariableValidation(condition="var.port >= 1", error_message="port >= 1"),
                    VariableValidation(condition="var.port <= 65535", error_message="port <= 65535"),
                ],
            )
        ],
    )
    var_tf = HCLRenderer(s).files()["variables.tf"]
    assert var_tf.count("validation {") == 2


# --- Backend rendering ------------------------------------------------------


def test_no_backend_means_no_backend_tf():
    s = Stack(name="t")
    files = HCLRenderer(s).files()
    assert "backend.tf" not in files


def test_s3_backend_renders():
    s = Stack(
        name="t",
        backend=S3Backend(
            bucket="my-state-bucket",
            key="acme/site.tfstate",
            region="us-west-2",
            dynamodb_table="tf-locks",
        ),
    )
    backend_tf = HCLRenderer(s).files()["backend.tf"]
    assert 'backend "s3"' in backend_tf
    assert 'bucket  = "my-state-bucket"' in backend_tf
    assert 'key     = "acme/site.tfstate"' in backend_tf
    assert 'region  = "us-west-2"' in backend_tf
    assert 'encrypt = true' in backend_tf
    assert 'dynamodb_table = "tf-locks"' in backend_tf


def test_gcs_backend_renders():
    s = Stack(name="t", backend=GCSBackend(bucket="my-state", prefix="acme/site"))
    backend_tf = HCLRenderer(s).files()["backend.tf"]
    assert 'backend "gcs"' in backend_tf
    assert 'bucket = "my-state"' in backend_tf
    assert 'prefix = "acme/site"' in backend_tf


def test_tfc_backend_with_workspace_name():
    s = Stack(
        name="t",
        backend=TerraformCloudBackend(organization="acme", workspace_name="acme-site"),
    )
    backend_tf = HCLRenderer(s).files()["backend.tf"]
    assert "cloud {" in backend_tf
    assert 'organization = "acme"' in backend_tf
    assert 'name = "acme-site"' in backend_tf


def test_tfc_backend_with_workspace_tags():
    s = Stack(
        name="t",
        backend=TerraformCloudBackend(organization="acme", workspace_tags=["prod", "site"]),
    )
    backend_tf = HCLRenderer(s).files()["backend.tf"]
    assert 'tags = ["prod", "site"]' in backend_tf


def test_local_backend_default_renders_empty():
    s = Stack(name="t", backend=LocalBackend())
    backend_tf = HCLRenderer(s).files()["backend.tf"]
    assert 'backend "local" {}' in backend_tf


def test_data_block_renders_with_data_keyword():
    """Service with kind='data' should emit `data "T" "ID"` not `resource ...`."""
    s = Stack(
        name="t",
        services=[
            Service(
                id="my_zip",
                type="archive_file",
                kind="data",
                category="compute",
                config={"type": "zip", "source_dir": "${path.module}/src", "output_path": "${path.module}/src.zip"},
            )
        ],
    )
    compute_tf = HCLRenderer(s).files()["compute.tf"]
    assert 'data "archive_file" "my_zip"' in compute_tf
    assert 'resource "archive_file"' not in compute_tf


def test_extra_providers_appear_in_versions_tf():
    s = Stack(
        name="t",
        provider="aws",
        extra_providers=[
            ProviderRequirement(local_name="archive", source="hashicorp/archive", version="~> 2.4"),
            ProviderRequirement(local_name="random", source="hashicorp/random", version="~> 3.6"),
        ],
    )
    versions_tf = HCLRenderer(s).files()["versions.tf"]
    # Main cloud provider still present
    assert 'aws = {' in versions_tf
    assert 'source  = "hashicorp/aws"' in versions_tf
    # Extras included
    assert 'archive = {' in versions_tf
    assert 'source  = "hashicorp/archive"' in versions_tf
    assert 'random = {' in versions_tf


def test_no_providers_means_no_versions_tf_block():
    """A stack without recognized providers and no extras emits an empty versions block."""
    s = Stack(name="t", provider="multi")  # 'multi' is not in the mapping
    versions_tf = HCLRenderer(s).files()["versions.tf"]
    # We still emit the file (with header), just no terraform { ... } block.
    assert "terraform {" not in versions_tf


def test_render_writes_to_disk(tmp_path):
    s = Stack(name="t", services=[Service(id="b", type="aws_s3_bucket", category="storage", config={"bucket": "x"})])
    written = HCLRenderer(s).write(tmp_path)
    names = sorted(p.name for p in written)
    assert "main.tf" in names
    assert "provider.tf" in names
    assert "storage.tf" in names
    assert (tmp_path / "main.tf").read_text().startswith("# Generated by infra-x")
