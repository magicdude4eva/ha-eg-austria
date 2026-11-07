#!/usr/bin/env python3
"""Privacy guard: fail if private data is about to enter the repository.

Checks every given file (or every tracked file with ``--all``) for:

* JWT-like strings (``eyJ...``-style three-part base64url tokens),
* Austrian metering point numbers (``AT`` + 31 digits) other than synthetic
  ones (grid operator code ``000000``),
* email addresses other than noreply / example / reserved test domains,
* forbidden file types (HAR captures, PDFs, databases, real CSV exports),
* entries of a private denylist kept outside the repository.

The denylist path comes from ``--denylist`` or the environment variable
``EG_PRIVACY_DENYLIST``. Its contents are never printed: a hit is reported as
``denylist entry #N`` with the file and line only, and the matching line is
not shown. Other hits are shown redacted.

Exit codes: 0 clean, 1 findings, 2 usage error.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata

DEFAULT_DENYLIST = "/data/eg-austria-research/private/denylist.txt"
MIN_DENY_LEN = 4
MAX_FILE_BYTES = 5 * 1024 * 1024

JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}")
# AT + 31 digits, optionally grouped by spaces or dots.
METERING_RE = re.compile(r"(?<![A-Za-z0-9])AT(?:[ .]?\d){31}(?!\d)")
SYNTHETIC_GRID_OPERATOR = "000000"
EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)"
)
ALLOWED_EMAIL_LOCALPARTS = {"noreply", "no-reply", "git", "actions"}
ALLOWED_EMAIL_DOMAINS = {
    "users.noreply.github.com",
    "noreply.github.com",
    "example.com",
    "example.org",
    "example.net",
}
ALLOWED_EMAIL_TLDS = {"example", "invalid", "test", "localhost"}
# Bot addresses GitHub puts into commit trailers (Dependabot "Signed-off-by").
ALLOWED_EMAILS = {"support@github.com"}

FORBIDDEN_SUFFIXES = {
    ".har",
    ".pdf",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".xlsx",
    ".xls",
    ".pcap",
    ".pem",
    ".key",
}
FORBIDDEN_NAMES = {"secrets.yaml", ".env", "denylist.txt", "analysis.md"}
# Synthetic CSVs are only allowed under tests/fixtures/.
CSV_ALLOWED_PREFIX = "tests/fixtures/"


@dataclass(frozen=True)
class Finding:
    """One privacy finding."""

    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        """Format as path:line: [rule] detail."""
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"{loc}: [{self.rule}] {self.detail}"


def fold(text: str) -> str:
    """Normalise text for denylist matching (case, umlauts, whitespace)."""
    text = text.casefold()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)


def redact(value: str) -> str:
    """Show only enough of a hit to locate it."""
    return value[:4] + "…" if len(value) > 4 else "…"


def load_denylist(path: str | None) -> tuple[list[str], int]:
    """Return folded denylist entries and the number of too-short entries."""
    if not path or not Path(path).is_file():
        return [], 0
    entries: list[str] = []
    short = 0
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        folded = fold(line)
        if len(folded) < MIN_DENY_LEN:
            short += 1
            continue
        entries.append(folded)
    return entries, short


def email_allowed(local: str, domain: str) -> bool:
    """Return True for noreply, example and reserved test addresses."""
    local = local.lower()
    domain = domain.lower()
    if local in ALLOWED_EMAIL_LOCALPARTS or f"{local}@{domain}" in ALLOWED_EMAILS:
        return True
    if domain in ALLOWED_EMAIL_DOMAINS or domain.endswith(".users.noreply.github.com"):
        return True
    return domain.rsplit(".", 1)[-1] in ALLOWED_EMAIL_TLDS


def scan_text(path: str, text: str, denylist: list[str]) -> list[Finding]:
    """Scan one file's text."""
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in JWT_RE.finditer(line):
            findings.append(Finding(path, lineno, "jwt", redact(m.group(0))))
        for m in METERING_RE.finditer(line):
            digits = re.sub(r"\D", "", m.group(0))
            if not digits.startswith(SYNTHETIC_GRID_OPERATOR):
                findings.append(
                    Finding(path, lineno, "metering-point", redact(m.group(0)))
                )
        for m in EMAIL_RE.finditer(line):
            if not email_allowed(m.group(1), m.group(2)):
                findings.append(Finding(path, lineno, "email", redact(m.group(0))))
        if denylist:
            folded = fold(line)
            for idx, entry in enumerate(denylist, start=1):
                if entry in folded:
                    findings.append(
                        Finding(path, lineno, "denylist", f"denylist entry #{idx}")
                    )
    return findings


def scan_name(path: str, denylist: list[str]) -> list[Finding]:
    """Check the file path itself."""
    findings: list[Finding] = []
    posix = path.replace(os.sep, "/")
    name = posix.rsplit("/", 1)[-1]
    suffix = Path(name).suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES or name.lower() in FORBIDDEN_NAMES:
        findings.append(Finding(path, 0, "forbidden-file", "file type not allowed"))
    if suffix == ".csv" and not posix.startswith(CSV_ALLOWED_PREFIX):
        findings.append(
            Finding(path, 0, "forbidden-file", "CSV only under tests/fixtures/")
        )
    folded = fold(posix)
    for idx, entry in enumerate(denylist, start=1):
        if entry in folded:
            findings.append(Finding(path, 0, "denylist", f"denylist entry #{idx}"))
    return findings


def scan_file(path: str, denylist: list[str], root: Path) -> list[Finding]:
    """Scan one file (name and content)."""
    full = root / path
    findings = scan_name(path, denylist)
    if not full.is_file():
        return findings
    if full.stat().st_size > MAX_FILE_BYTES:
        findings.append(Finding(path, 0, "too-large", "file over 5 MB"))
        return findings
    data = full.read_bytes()
    if b"\0" in data:
        # Binary: only filename checks apply (icons etc.).
        return findings
    findings.extend(scan_text(path, data.decode("utf-8", "replace"), denylist))
    return findings


def git_files(root: Path) -> list[str]:
    """Tracked plus untracked-but-not-ignored files."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return sorted({p for p in out.decode().split("\0") if p})


def git_log_text(root: Path, rev_range: str) -> str:
    """Commit messages and author/committer lines for a revision range."""
    return subprocess.run(
        ["git", "log", "--format=%an <%ae>%n%cn <%ce>%n%B", rev_range],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", help="files to scan")
    parser.add_argument("--all", action="store_true", help="scan the whole tree")
    parser.add_argument("--git-log", metavar="RANGE", help="also scan commits")
    parser.add_argument(
        "--commit-msg", metavar="FILE", help="scan a commit message file"
    )
    parser.add_argument("--denylist", help="private denylist file")
    parser.add_argument(
        "--require-denylist",
        action="store_true",
        help="fail if the denylist is missing",
    )
    parser.add_argument("--list", action="store_true", help="print scanned files")
    args = parser.parse_args(argv)

    root = Path.cwd()
    deny_path = args.denylist or os.environ.get("EG_PRIVACY_DENYLIST")
    if deny_path is None and Path(DEFAULT_DENYLIST).is_file():
        deny_path = DEFAULT_DENYLIST
    denylist, short = load_denylist(deny_path)
    if not denylist:
        if args.require_denylist:
            print("privacy-guard: denylist required but not found", file=sys.stderr)
            return 2
        print("privacy-guard: no private denylist, pattern checks only")
    if short:
        print(f"privacy-guard: ignored {short} denylist entries shorter than 4")

    findings: list[Finding] = []
    files = git_files(root) if args.all else list(args.files)
    for path in files:
        findings.extend(scan_file(path, denylist, root))
    if args.git_log:
        findings.extend(
            scan_text(
                f"git-log({args.git_log})", git_log_text(root, args.git_log), denylist
            )
        )
    if args.commit_msg:
        text = Path(args.commit_msg).read_text(encoding="utf-8", errors="replace")
        findings.extend(scan_text("commit-message", text, denylist))

    if args.list:
        for path in files:
            print(f"  {path}")
    for finding in findings:
        print(finding)
    status = "FAILED" if findings else "ok"
    print(
        f"privacy-guard: {status}: {len(files)} files, "
        f"{len(denylist)} denylist entries, {len(findings)} findings"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
