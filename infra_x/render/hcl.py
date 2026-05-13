"""
HCL renderer.

Turns a `Stack` IR into Terraform `.tf` files on disk.

We deliberately don't depend on a Python HCL library — the formats they emit are
inconsistent and Terraform's HCL2 has a few quirks (heredocs, raw expressions,
nested blocks) that are easier to handle ourselves than fight a library over.
The grammar we emit is small but covers what every blueprint here needs.

File layout
-----------
- versions.tf      — required_providers, required_version
- provider.tf      — provider "..." { ... } block(s)
- variables.tf     — every Variable + its validation blocks
- outputs.tf       — every Output
- backend.tf       — terraform { backend "X" { ... } }   (only if Stack.backend set)
- <category>.tf    — one file per non-empty Service category (networking.tf,
                     security.tf, compute.tf, ...). Anything in `other` falls
                     into main.tf.
- main.tf          — uncategorized resources (always emitted, possibly empty)

If you need richer features later (dynamic blocks, complex for-each), add them
to the dispatcher in `_render_value`.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

# HCL bare identifier rules for object keys / arguments. Anything outside this
# (dots, slashes, hyphens that lead, etc.) must be quoted, e.g.
#   tags = { "kubernetes.io/role/elb" = "1" }
_HCL_BARE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _format_key(key: str) -> str:
    """Return an HCL-safe rendering of a map/object key (quoting if needed)."""
    if _HCL_BARE_KEY.match(key):
        return key
    return _quote(key)

from infra_x import __version__
from infra_x.ir.models import (
    BackendConfig,
    GCSBackend,
    LocalBackend,
    Output,
    S3Backend,
    Service,
    Stack,
    TerraformCloudBackend,
    Variable,
)

# --- value rendering ---------------------------------------------------------


def _is_raw_expr(s: str) -> str | None:
    """
    If `s` is *entirely* a single `${...}` expression, return the inner expr.
    Otherwise return None (caller will emit a quoted HCL string literal).

    Critically, this must distinguish between:
        ${var.foo}                       → raw expression  → `var.foo`
        ${var.foo}-${var.bar}            → string literal  → `"${var.foo}-${var.bar}"`
        ${jsonencode({k = "v"})}         → raw expression  → `jsonencode({k = "v"})`

    The naive regex `^\\${(.+)}$` is greedy and incorrectly matches the second
    case. We instead walk the string and verify that the `}` matching the
    opening `${` is the very last character.
    """
    s = s.strip()
    if len(s) < 4 or not s.startswith("${") or not s.endswith("}"):
        return None
    depth = 0
    for i, ch in enumerate(s):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                # First time depth returns to 0 — this is the close that pairs
                # with the opening `${`. If it's the last character, the whole
                # string is a single raw expression. Otherwise it's a literal
                # containing interpolations.
                return s[2:i] if i == len(s) - 1 else None
    # Unbalanced braces — treat as a string literal to be safe.
    return None


def _render_value(v: Any, indent: int = 0) -> str:
    """Render a Python value to an HCL expression."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        expr = _is_raw_expr(v)
        if expr is not None:
            return expr
        # Heredoc for multi-line strings (cleaner than escaping newlines)
        if "\n" in v:
            return "<<-EOT\n" + v + "\nEOT"
        return _quote(v)
    if isinstance(v, list):
        if not v:
            return "[]"
        items = [_render_value(x, indent + 2) for x in v]
        # Inline if short
        if all("\n" not in x for x in items) and sum(len(x) for x in items) < 60:
            return "[" + ", ".join(items) + "]"
        pad = " " * (indent + 2)
        return "[\n" + ",\n".join(pad + x for x in items) + "\n" + " " * indent + "]"
    if isinstance(v, dict):
        return _render_object(v, indent)
    raise TypeError(f"cannot render {type(v).__name__} to HCL: {v!r}")


def _quote(s: str) -> str:
    """Quote a string as an HCL string literal."""
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    # Preserve `${...}` interpolations inside otherwise-plain strings.
    return f'"{escaped}"'


def _render_object(d: dict[str, Any], indent: int = 0) -> str:
    """Render a dict as an HCL object literal `{ k = v, ... }`.

    Keys that aren't valid HCL bare identifiers (e.g. `kubernetes.io/role/elb`)
    are quoted automatically.
    """
    if not d:
        return "{}"
    pad = " " * (indent + 2)
    formatted = [(_format_key(str(k)), v) for k, v in d.items()]
    width = max((len(k) for k, _ in formatted), default=0)
    lines = [
        f"{pad}{k.ljust(width)} = {_render_value(v, indent + 2)}"
        for k, v in formatted
    ]
    return "{\n" + "\n".join(lines) + "\n" + " " * indent + "}"


# --- block rendering ---------------------------------------------------------


def _render_block_body(config: dict[str, Any], indent: int) -> str:
    """
    Render a resource body. Convention:
      - scalar / list / dict values become `name = ...`
      - a key whose value is `{"_block": {...}}` becomes a nested block
      - a key whose value is `{"_blocks": [{...}, ...]}` becomes repeated blocks
    """
    pad = " " * indent
    out: list[str] = []

    # Pass 1: arguments (scalars & dicts that aren't blocks)
    plain: list[tuple[str, Any]] = []
    blocks: list[tuple[str, Any]] = []
    for k, v in config.items():
        if isinstance(v, dict) and ("_block" in v or "_blocks" in v):
            blocks.append((k, v))
        else:
            plain.append((k, v))

    width = max((len(k) for k, _ in plain), default=0)
    for k, v in plain:
        out.append(f"{pad}{k.ljust(width)} = {_render_value(v, indent)}")

    for k, v in blocks:
        if "_block" in v:
            inner = v["_block"]
            out.append("")
            out.append(f"{pad}{k} {{")
            out.append(_render_block_body(inner, indent + 2))
            out.append(f"{pad}}}")
        elif "_blocks" in v:
            for inner in v["_blocks"]:
                out.append("")
                out.append(f"{pad}{k} {{")
                out.append(_render_block_body(inner, indent + 2))
                out.append(f"{pad}}}")
    return "\n".join(out)


def _render_resource(svc: Service) -> str:
    """`resource "TYPE" "ID" { ... }` or `data "TYPE" "ID" { ... }`."""
    body = _render_block_body(svc.config, indent=2)
    keyword = svc.kind  # "resource" or "data"
    head = f'{keyword} "{svc.type}" "{svc.id}" {{'
    parts = [head]
    if body:
        parts.append(body)
    if svc.depends_on:
        # We can't always know the resource type of a dep without a stack-wide
        # map, but blueprints reference deps via ${...} in config, which is
        # idiomatic. We leave depends_on for explicit cases only.
        deps_repr = ", ".join(svc.depends_on)
        parts.append("")
        parts.append(f"  # depends_on (logical): {deps_repr}")
    parts.append("}")
    return "\n".join(parts)


def _render_variable(v: Variable) -> str:
    body: list[str] = [f'  type        = {v.type}']
    if v.description:
        body.append(f"  description = {_quote(v.description)}")
    if v.default is not None:
        body.append(f"  default     = {_render_value(v.default, indent=2)}")
    if v.sensitive:
        body.append("  sensitive   = true")
    for val in v.validations:
        body.append("")
        body.append("  validation {")
        body.append(f"    condition     = {val.condition}")
        body.append(f"    error_message = {_quote(val.error_message)}")
        body.append("  }")
    return f'variable "{v.name}" {{\n' + "\n".join(body) + "\n}"


def _render_output_value(value: str) -> str:
    """
    Output values arrive as raw strings and can take three shapes. We have to
    pick the right HCL form for each, otherwise terraform will refuse to parse:

      - bare HCL expression:        aws_s3_bucket.site.bucket_domain_name
                                    → emit verbatim
      - single interpolation:       ${aws_s3_bucket.site.arn}
                                    → strip ${ } wrapper and emit inner expr
      - template with literal text: ${var.region}-docker.pkg.dev/${var.proj}
                                    → wrap in quotes as an HCL string literal

    The third case is the one we used to get wrong — emitting it bare gave
    `value = ${var.region}-docker.pkg.dev/...` which terraform parses as garbage.
    """
    value = value.strip()
    if "${" not in value:
        return value
    inner = _is_raw_expr(value)
    if inner is not None:
        return inner
    return _quote(value)


def _render_output(o: Output) -> str:
    body = [f"  value       = {_render_output_value(o.value)}"]
    if o.description:
        body.append(f"  description = {_quote(o.description)}")
    if o.sensitive:
        body.append("  sensitive   = true")
    return f'output "{o.name}" {{\n' + "\n".join(body) + "\n}"


def _provider_block(provider: str, region: str | None) -> str:
    if provider == "aws":
        body = f'  region = {_quote(region or "us-east-1")}'
        return "provider \"aws\" {\n" + body + "\n}"
    if provider == "gcp":
        body = "  project = var.project_id\n"
        body += f'  region  = {_quote(region or "us-central1")}'
        return 'provider "google" {\n' + body + "\n}"
    if provider == "azure":
        return 'provider "azurerm" {\n  features {}\n}'
    if provider == "cloudflare":
        return 'provider "cloudflare" {\n  # api_token from CLOUDFLARE_API_TOKEN\n}'
    return f'# provider "{provider}" — configure manually'


def _required_providers_block(provider: str, extra: list | None = None) -> str:
    mapping = {
        "aws": ("aws", "hashicorp/aws", "~> 5.0"),
        "gcp": ("google", "hashicorp/google", "~> 5.0"),
        "azure": ("azurerm", "hashicorp/azurerm", "~> 3.0"),
        "cloudflare": ("cloudflare", "cloudflare/cloudflare", "~> 4.0"),
    }
    entries: list[tuple[str, str, str]] = []
    if provider in mapping:
        entries.append(mapping[provider])
    for ep in extra or []:
        entries.append((ep.local_name, ep.source, ep.version))
    if not entries:
        return ""

    lines = ["terraform {", '  required_version = ">= 1.5.0"', "  required_providers {"]
    for name, source, ver in entries:
        lines.append(f"    {name} = {{")
        lines.append(f'      source  = "{source}"')
        lines.append(f'      version = "{ver}"')
        lines.append("    }")
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines)


# --- backend rendering -------------------------------------------------------


def _render_backend(backend: BackendConfig) -> str:
    """Render a `terraform { backend "X" { ... } }` (or `cloud { ... }`) block."""
    if isinstance(backend, S3Backend):
        body = [
            f'    bucket  = "{backend.bucket}"',
            f'    key     = "{backend.key}"',
            f'    region  = "{backend.region}"',
            f"    encrypt = {'true' if backend.encrypt else 'false'}",
        ]
        if backend.dynamodb_table:
            body.append(f'    dynamodb_table = "{backend.dynamodb_table}"')
        return (
            "terraform {\n"
            '  backend "s3" {\n'
            + "\n".join(body)
            + "\n  }\n"
            "}"
        )
    if isinstance(backend, GCSBackend):
        return (
            "terraform {\n"
            '  backend "gcs" {\n'
            f'    bucket = "{backend.bucket}"\n'
            f'    prefix = "{backend.prefix}"\n'
            "  }\n"
            "}"
        )
    if isinstance(backend, TerraformCloudBackend):
        body = [f'    organization = "{backend.organization}"']
        if backend.workspace_name:
            body.append("    workspaces {")
            body.append(f'      name = "{backend.workspace_name}"')
            body.append("    }")
        elif backend.workspace_tags:
            body.append("    workspaces {")
            tags_repr = ", ".join(f'"{t}"' for t in backend.workspace_tags)
            body.append(f"      tags = [{tags_repr}]")
            body.append("    }")
        return (
            "terraform {\n"
            "  cloud {\n"
            + "\n".join(body)
            + "\n  }\n"
            "}"
        )
    if isinstance(backend, LocalBackend):
        if backend.path:
            return (
                "terraform {\n"
                '  backend "local" {\n'
                f'    path = "{backend.path}"\n'
                "  }\n"
                "}"
            )
        return 'terraform {\n  backend "local" {}\n}'
    raise TypeError(f"unknown backend type: {type(backend).__name__}")


# --- public API --------------------------------------------------------------


class HCLRenderer:
    """Render a Stack IR into a directory of .tf files."""

    def __init__(self, stack: Stack):
        self.stack = stack

    def files(self) -> dict[str, str]:
        """Return a mapping of relative path -> file contents."""
        s = self.stack

        header = (
            f"# Generated by infra-x v{__version__}\n"
            f"# Stack: {s.name}\n"
            + (f"# Blueprint: {s.blueprint_id}\n" if s.blueprint_id else "")
            + (f"# Description: {s.description}\n" if s.description else "")
            + "# DO NOT EDIT BY HAND if you plan to regenerate. Use `infra-x regen`.\n"
        )

        out: dict[str, str] = {}

        # versions.tf — required_providers (cloud + any extras the blueprint declared)
        out["versions.tf"] = (
            header + "\n" + _required_providers_block(s.provider, s.extra_providers) + "\n"
        )

        # provider.tf
        out["provider.tf"] = header + "\n" + _provider_block(s.provider, s.region) + "\n"

        # backend.tf (only if a backend is configured)
        if s.backend is not None:
            out["backend.tf"] = header + "\n" + _render_backend(s.backend) + "\n"

        # variables.tf
        if s.variables:
            out["variables.tf"] = (
                header + "\n" + "\n\n".join(_render_variable(v) for v in s.variables) + "\n"
            )

        # outputs.tf
        if s.outputs:
            out["outputs.tf"] = (
                header + "\n" + "\n\n".join(_render_output(o) for o in s.outputs) + "\n"
            )

        # Resource files: one per non-empty category, plus main.tf for `other`.
        # Stable iteration order so output is deterministic.
        category_order = [
            "networking", "security", "iam", "compute", "storage",
            "database", "cdn", "observability", "dns", "other",
        ]
        groups: OrderedDict[str, list[Service]] = OrderedDict(
            (cat, []) for cat in category_order
        )
        for svc in s.services:
            groups.setdefault(svc.category, []).append(svc)

        # `other` is always emitted as main.tf (even if empty, so consumers can
        # rely on its existence when they edit by hand).
        for cat, svcs in groups.items():
            if cat == "other":
                continue
            if not svcs:
                continue
            out[f"{cat}.tf"] = (
                header + "\n" + "\n\n".join(_render_resource(svc) for svc in svcs) + "\n"
            )

        other_svcs = groups.get("other", [])
        main_body = "\n\n".join(_render_resource(svc) for svc in other_svcs)
        out["main.tf"] = header + "\n" + (main_body + "\n" if main_body else "")

        return out

    def write(self, dest: str | Path) -> list[Path]:
        """Write files to `dest`. Returns list of paths written."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for rel, content in self.files().items():
            p = dest / rel
            p.write_text(content)
            written.append(p)
        return written


def render_stack(stack: Stack, dest: str | Path) -> list[Path]:
    """Convenience: render and write."""
    return HCLRenderer(stack).write(dest)
