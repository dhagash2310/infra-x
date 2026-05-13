"""Tests for the graph IR (Pydantic models)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from infra_x.ir.models import Connection, Service, Stack, Variable


def test_service_id_must_be_valid_tf_identifier():
    Service(id="my_bucket", type="aws_s3_bucket")
    with pytest.raises(ValidationError):
        Service(id="1bad-id", type="aws_s3_bucket")
    with pytest.raises(ValidationError):
        Service(id="bad id with space", type="aws_s3_bucket")


def test_service_type_must_be_lowercase():
    Service(id="x", type="aws_s3_bucket")
    with pytest.raises(ValidationError):
        Service(id="x", type="AWS_S3_Bucket")


def test_stack_name_validation():
    Stack(name="my-stack")
    with pytest.raises(ValidationError):
        Stack(name="MyStack")  # uppercase not allowed
    with pytest.raises(ValidationError):
        Stack(name="-leading-hyphen")


def test_connection_alias_supports_from_keyword():
    """`from` is a Python keyword; we must support both forms."""
    s = Stack(
        name="t",
        services=[
            Service(id="a", type="aws_s3_bucket"),
            Service(id="b", type="aws_s3_bucket"),
        ],
    )
    s.connections.append(Connection.model_validate({"from": "a", "to": "b", "kind": "depends_on"}))
    s.connections.append(Connection(from_id="a", to_id="b"))
    assert len(s.connections) == 2


def test_stack_rejects_dangling_dependency():
    with pytest.raises(ValidationError):
        Stack(
            name="t",
            services=[Service(id="a", type="aws_s3_bucket", depends_on=["ghost"])],
        )


def test_stack_rejects_dangling_connection():
    with pytest.raises(ValidationError):
        Stack(
            name="t",
            services=[Service(id="a", type="aws_s3_bucket")],
            connections=[Connection(from_id="a", to_id="ghost")],
        )


def test_variable_default_can_be_any_json_value():
    Variable(name="x", type="string", default="hello")
    Variable(name="y", type="number", default=42)
    Variable(name="z", type="list(string)", default=["a", "b"])
    Variable(name="w", type="map(any)", default={"k": "v"})
