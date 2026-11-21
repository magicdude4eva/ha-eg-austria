"""Tests for scripts/check_coverage.py (issue #35)."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_coverage", Path(__file__).parents[1] / "scripts" / "check_coverage.py"
)
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["check_coverage"] = gate
_SPEC.loader.exec_module(gate)

PKG = "custom_components/eg_austria/"


def _file(covered: int, total: int, missing: list[int] | None = None) -> dict:
    return {
        "summary": {"covered_lines": covered, "num_statements": total},
        "missing_lines": missing or [],
    }


def _write(tmp_path: Path, files: dict) -> str:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"files": files}), encoding="utf-8")
    return str(path)


def test_passes_within_limits(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """100% config flow, other modules above 95%, empty modules count as 100%."""

    report = _write(
        tmp_path,
        {
            PKG + "config_flow.py": _file(10, 10),
            PKG + "repairs.py": _file(955, 1000),
            PKG + "const.py": _file(0, 0),
        },
    )
    assert gate.main([report]) == 0
    assert "3 modules within limits" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        # 95% exactly is not above 95%.
        ({PKG + "repairs.py": _file(95, 100)}, "repairs.py: 95.00% <= 95%"),
        # 99.9% is not 100% for the config flow; missing lines are listed.
        (
            {PKG + "config_flow.py": _file(999, 1000, [160, 227])},
            "config_flow.py: 99.90% < 100% (missing: 160, 227)",
        ),
    ],
)
def test_fails_below_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture, files: dict, expected: str
) -> None:
    """A module below its limit fails the gate and is named."""

    assert gate.main([_write(tmp_path, files)]) == 1
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize("content", [None, "not json", '{"files": {}}', "[]"])
def test_bad_report(tmp_path: Path, content: str | None) -> None:
    """A missing, broken or empty report is a usage error, not a pass."""

    path = tmp_path / "coverage.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert gate.main([str(path)]) == 2
