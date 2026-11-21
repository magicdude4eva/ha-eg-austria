#!/usr/bin/env python3
"""Coverage gate: fail when a module drops below the quality-scale limits.

Reads the JSON report of ``coverage json`` (default ``coverage.json``) and
checks every measured module:

* ``config_flow.py`` must be at 100% (Bronze ``config-flow-test-coverage``),
* every other module must be above 95% (Silver ``test-coverage``).

The total is enforced by ``fail_under`` in ``pyproject.toml``; this script
adds the per-module limits that coverage itself does not have.

Exit codes: 0 all limits met, 1 a module is below its limit, 2 bad report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

MODULE_MIN = 95.0  # a module must be above this
FULL: frozenset[str] = frozenset({"config_flow.py"})  # these must be at 100%


def percent(summary: dict[str, int]) -> float:
    """Exact line coverage in percent (no rounding: 95.4% is not 95%)."""

    statements = summary["num_statements"]
    if statements == 0:
        return 100.0
    return 100.0 * summary["covered_lines"] / statements


def failures(report: dict[str, object]) -> list[str]:
    """One message per module below its limit."""

    files = report["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError("no measured files in the report")
    out: list[str] = []
    for path, data in sorted(files.items()):
        value = percent(data["summary"])
        name = Path(path).name
        if name in FULL:
            if value < 100.0:
                missing = ", ".join(map(str, data.get("missing_lines", [])))
                out.append(f"{path}: {value:.2f}% < 100% (missing: {missing})")
        elif value <= MODULE_MIN:
            out.append(f"{path}: {value:.2f}% <= {MODULE_MIN:g}%")
    return out


def main(argv: list[str] | None = None) -> int:
    """Check the report; print one line per failing module."""

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", nargs="?", default="coverage.json")
    args = parser.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        found = failures(report)
    except (OSError, ValueError, KeyError, TypeError) as err:
        print(f"check_coverage: cannot read {args.report}: {err}", file=sys.stderr)
        return 2
    for line in found:
        print(line)
    if found:
        return 1
    print(f"check_coverage: {len(report['files'])} modules within limits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
