"""Tests for scripts/privacy_guard.py.

Sensitive-looking samples are assembled at runtime so this file itself stays
clean for the guard.
"""

import importlib.util
from pathlib import Path
import sys

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "privacy_guard", Path(__file__).parents[1] / "scripts" / "privacy_guard.py"
)
guard = importlib.util.module_from_spec(_SPEC)
sys.modules["privacy_guard"] = guard
_SPEC.loader.exec_module(guard)

JWT = "ey" + "J" + "hbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxIn0." + "c2lnbmF0dXJlXzEyMw"
REAL_MP = "A" + "T" + "007000" + "9" * 25
SYNTH_MP = "A" + "T" + "0" * 27 + "1001"
BAD_EMAIL = "someone" + "@" + "mail.example-isp.at"


def rules(text: str, deny: list[str] | None = None) -> list[str]:
    """Return the rule ids found in text."""
    return [f.rule for f in guard.scan_text("x", text, deny or [])]


def test_jwt_detected() -> None:
    """JWT-shaped strings fail."""
    assert rules(f"cookie: portal.jwtToken={JWT}") == ["jwt"]


def test_metering_point_real_vs_synthetic() -> None:
    """Real-looking metering point numbers fail; synthetic ones pass."""
    assert rules(REAL_MP) == ["metering-point"]
    assert rules(SYNTH_MP) == []
    spaced = REAL_MP[:2] + " " + REAL_MP[2:8] + " " + REAL_MP[8:]
    assert rules(spaced) == ["metering-point"]


@pytest.mark.parametrize(
    "addr",
    [
        "member@example.invalid",
        "user@example.com",
        "noreply@anthropic.com",
        "123+someone@users.noreply.github.com",
    ],
)
def test_allowed_emails(addr: str) -> None:
    """Noreply, example and reserved addresses pass."""
    assert rules(addr) == []


def test_other_email_fails() -> None:
    """Any other address fails."""
    assert rules(BAD_EMAIL) == ["email"]


def test_denylist_folding_and_no_leak(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Denylist matches case/umlaut-insensitively and never prints the entry."""
    secret = "Mus" + "terstraße"
    deny = tmp_path / "deny.txt"
    deny.write_text(f"# comment\nab\n{secret}\n", encoding="utf-8")
    entries, short = guard.load_denylist(str(deny))
    assert short == 1
    (tmp_path / "a.md").write_text("Lives at MUSTERSTRASSE 1\n", encoding="utf-8")
    found = guard.scan_file("a.md", entries, tmp_path)
    assert [f.rule for f in found] == ["denylist"]
    assert "terstr" not in str(found[0]).lower()

    # Full CLI run: output must not contain the entry or the matching line.
    (tmp_path / "b.txt").write_text(secret, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    rc = guard.main(["--denylist", str(deny), "a.md", "b.txt"])
    out = capsys.readouterr().out.lower()
    assert rc == 1
    assert "terstr" not in out
    assert "denylist entry #1" in out


@pytest.mark.parametrize(
    "name",
    ["capture.har", "Kontoblatt.pdf", "export.csv", "secrets.yaml", "analysis.md"],
)
def test_forbidden_files(name: str) -> None:
    """Private file types are blocked by name."""
    assert [f.rule for f in guard.scan_name(name, [])] == ["forbidden-file"]


def test_fixture_csv_allowed() -> None:
    """Synthetic CSVs under tests/fixtures are allowed."""
    assert guard.scan_name("tests/fixtures/bezug_tag.csv", []) == []


def test_repository_is_clean() -> None:
    """The repository passes its own pattern checks (denylist runs separately)."""
    root = Path(__file__).parents[1]
    findings = []
    for path in guard.git_files(root):
        findings.extend(guard.scan_file(path, [], root))
    assert findings == []


def test_metering_point_length() -> None:
    """Synthetic sample from the test plan has the real length (AT + 31 digits)."""
    assert len(SYNTH_MP) == len(REAL_MP) == 33
