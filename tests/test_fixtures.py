"""The committed fixtures are exactly what the generator produces (spec 07)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys

from .conftest import FIXTURES

_SPEC = importlib.util.spec_from_file_location(
    "fixture_generate", FIXTURES / "generate.py"
)
generate = importlib.util.module_from_spec(_SPEC)
sys.modules["fixture_generate"] = generate
_SPEC.loader.exec_module(generate)


def test_committed_fixtures_match_generator(tmp_path: Path) -> None:
    """Regenerating gives byte-identical files and no stray JSON files."""

    assert generate.main(["--out", str(tmp_path)]) == 0
    generated = {p.name: p.read_bytes() for p in tmp_path.glob("*.json")}
    committed = {p.name: p.read_bytes() for p in FIXTURES.glob("*.json")}
    assert sorted(committed) == sorted(generated)
    for name, data in generated.items():
        assert committed[name] == data, (
            f"{name} is stale: run tests/fixtures/generate.py"
        )


def test_fixtures_use_synthetic_ids_only() -> None:
    """Only the synthetic member and metering point numbers appear."""

    for path in FIXTURES.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        for token in ("eyJ", "ecapi_"):
            assert token not in text, path.name
        for number in re.findall(r"AT\d{31}", text):
            assert number.startswith("AT000000"), path.name
