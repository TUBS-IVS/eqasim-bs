"""Policy lint: no server address or credential-bearing URL in the deploy tooling.

The repository is public. On 2026-11-11 the server deploy scripts were found to carry
the run server's IP address and SSH user as parameter defaults, and the inherited
upstream ``docs/verify_data.py`` carried a commented-out URL with an embedded password.
Both are addresses of real systems and must come from the caller's environment
(``scripts/server_target.ps1``), never from a tracked file.

The scan walks an explicit list of first-party files (never the whole tree), and each
pattern has a positive control so a regex that silently matches nothing cannot pass.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Dotted-quad IPv4 literal. The deploy scripts have no legitimate use for one: version
# strings there have at most three components.
IPV4_LITERAL = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

# URL whose authority part carries "user:password@".
URL_WITH_PASSWORD = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@\"']+:[^/\s@\"']+@", re.IGNORECASE)

# A PowerShell parameter default that pins the server or its user to a literal.
LITERAL_SERVER_DEFAULT = re.compile(r"\$Server(?:Host|User)\s*=\s*[\"'][^\"'$]+[\"']")


def _deploy_scripts() -> list[Path]:
    return sorted((REPO_ROOT / "scripts").glob("*.ps1"))


def _verify_data_scripts() -> list[Path]:
    return [REPO_ROOT / "docs" / "verify_data.py", REPO_ROOT / "scripts" / "verify_data.py"]


@pytest.mark.parametrize(
    ("pattern", "offending", "clean"),
    [
        (IPV4_LITERAL, 'ssh "admin@10.20.30.40"', "rsync 3.2.7 and tmux 3.4"),
        (URL_WITH_PASSWORD, '# "ftp://someone:hunter2@files.example.org/a.7z"', '"https://example.org/a:b@c"'),
        (LITERAL_SERVER_DEFAULT, '[string]$ServerHost = "runbox",', "[string]$ServerHost = $env:EQASIM_SERVER_HOST,"),
    ],
)
def test_patterns_detect_their_idiom(pattern: re.Pattern[str], offending: str, clean: str) -> None:
    assert pattern.search(offending), "positive control: the pattern must match the leaked idiom"
    assert not pattern.search(clean), "negative control: the pattern must not match the accepted form"


def test_deploy_scripts_carry_no_server_address() -> None:
    scripts = _deploy_scripts()
    assert scripts, "no PowerShell deploy scripts found; the scan would be vacuous"
    offenders = []
    for path in scripts:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if IPV4_LITERAL.search(line) or LITERAL_SERVER_DEFAULT.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{number}")
    assert not offenders, (
        "server address or user pinned in a tracked deploy script (read it from "
        f"EQASIM_SERVER_HOST / EQASIM_SERVER_USER via scripts/server_target.ps1): {offenders}"
    )


def test_data_verification_scripts_carry_no_password_url() -> None:
    offenders = []
    for path in _verify_data_scripts():
        assert path.is_file(), f"expected verification script is missing: {path}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if URL_WITH_PASSWORD.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{number}")
    assert not offenders, f"URL with an embedded password in a tracked file: {offenders}"
