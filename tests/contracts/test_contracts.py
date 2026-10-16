from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from packages.contracts.validator import (
    load_schema,
    schema_validator,
    validate_document,
    validation_errors,
)

FIXTURES = Path("tests/fixtures/contracts")


def _documents(directory: str) -> list[tuple[Path, dict[str, object]]]:
    return [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((FIXTURES / directory).glob("*.json"))
    ]


def test_canonical_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(load_schema())


@pytest.mark.parametrize(("path", "document"), _documents("valid"), ids=lambda value: str(value))
def test_valid_contract_fixtures(path: Path, document: dict[str, object]) -> None:
    validate_document(document)
    assert path.name


@pytest.mark.parametrize(("path", "document"), _documents("invalid"), ids=lambda value: str(value))
def test_invalid_contract_fixtures(path: Path, document: dict[str, object]) -> None:
    assert validation_errors(document), f"expected invalid fixture to fail: {path}"


def test_schema_validator_is_compiled_once() -> None:
    assert schema_validator() is schema_validator()
