"""Tests for the --backend shorthand parser."""

from __future__ import annotations

import pytest

from infra_x.backend import BackendParseError, parse_backend_shorthand
from infra_x.ir.models import GCSBackend, LocalBackend, S3Backend, TerraformCloudBackend


def test_local_bare():
    b = parse_backend_shorthand("local")
    assert isinstance(b, LocalBackend)
    assert b.path is None


def test_local_with_path():
    b = parse_backend_shorthand("local:./state/tf.tfstate")
    assert isinstance(b, LocalBackend)
    assert b.path == "./state/tf.tfstate"


def test_s3_minimal():
    b = parse_backend_shorthand("s3://my-bucket/path/state.tfstate")
    assert isinstance(b, S3Backend)
    assert b.bucket == "my-bucket"
    assert b.key == "path/state.tfstate"
    assert b.region == "us-east-1"  # default
    assert b.encrypt is True


def test_s3_with_query_options():
    b = parse_backend_shorthand(
        "s3://my-bucket/sites/acme.tfstate?region=eu-west-2&lock=tf-locks&encrypt=false"
    )
    assert isinstance(b, S3Backend)
    assert b.region == "eu-west-2"
    assert b.dynamodb_table == "tf-locks"
    assert b.encrypt is False


def test_s3_auto_appends_tfstate_when_missing():
    b = parse_backend_shorthand("s3://my-bucket/sites/acme")
    assert isinstance(b, S3Backend)
    assert b.key.endswith(".tfstate")


def test_s3_rejects_missing_bucket():
    with pytest.raises(BackendParseError):
        parse_backend_shorthand("s3:///path/state.tfstate")


def test_s3_rejects_missing_key():
    with pytest.raises(BackendParseError):
        parse_backend_shorthand("s3://my-bucket")


def test_gcs_minimal():
    b = parse_backend_shorthand("gcs://my-bucket/stacks/acme")
    assert isinstance(b, GCSBackend)
    assert b.bucket == "my-bucket"
    assert b.prefix == "stacks/acme"


def test_gcs_rejects_missing_prefix():
    with pytest.raises(BackendParseError):
        parse_backend_shorthand("gcs://my-bucket")


def test_tfc_with_workspace_name():
    b = parse_backend_shorthand("tfc://acme-corp/acme-site")
    assert isinstance(b, TerraformCloudBackend)
    assert b.organization == "acme-corp"
    assert b.workspace_name == "acme-site"
    assert b.workspace_tags == []


def test_tfc_with_tags():
    b = parse_backend_shorthand("tfc://acme-corp?tags=prod,site")
    assert isinstance(b, TerraformCloudBackend)
    assert b.organization == "acme-corp"
    assert b.workspace_name is None
    assert b.workspace_tags == ["prod", "site"]


def test_tfc_rejects_missing_workspace_and_tags():
    with pytest.raises(BackendParseError):
        parse_backend_shorthand("tfc://acme-corp")


def test_unknown_scheme_rejected():
    with pytest.raises(BackendParseError):
        parse_backend_shorthand("azure://blob/state")


def test_empty_string_rejected():
    with pytest.raises(BackendParseError):
        parse_backend_shorthand("")
