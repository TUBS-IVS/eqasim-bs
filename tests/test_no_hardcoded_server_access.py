"""Tests for scripts/check_no_secrets.py and the deploy-script address lint.

Defects caught: a server address, an SSH login target, a URL with an embedded password or
a private key reaching a commit of this public repository, including as a default in the
deploy scripts; a scanner rule that silently matches nothing; an installed pre-commit hook
that does not actually refuse the commit.

The offending idioms below are synthetic. Their lines carry the scanner's allow marker,
because this file is itself scanned when it is committed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from scripts import check_no_secrets as scanner

REPO_ROOT = Path(__file__).resolve().parents[1]

# A PowerShell parameter default that pins the server or its user to a literal. Specific
# to the deploy scripts, so it lives here and not in the general scanner.
LITERAL_SERVER_DEFAULT = re.compile(r"\$Server(?:Host|User)\s*=\s*[\"'][^\"'$]+[\"']")


def _rules(line: str) -> set[str]:
    return {finding.rule for finding in scanner.scan_line(line)}


@pytest.mark.parametrize(
    ("rule", "offending", "clean"),
    [
        ("password-url", '"ftp://someone:hunter2@files.example.org/a.7z"', '"https://example.org/a:b@c"'),  # secret-scan: allow
        ("public-ipv4", "Server: 8.8.4.4, 64 vCPU", "127.0.0.1, 192.168.1.50, rsync 3.2.7, JDK 25.0.4.1, jdk-25.0.4.101-hotspot"),  # secret-scan: allow
        ("public-ipv4", "The run server is 8.8.4.4.", "release 1.2.3.4.5 of the tool"),  # secret-scan: allow
        ("public-ipv4", "Run server (Linux VM): 8.8.4.4 and the build server 8.8.4.4", 'openjdk version "25.0.4.1"'),  # secret-scan: allow
        ("private-key", "-----BEGIN PGP PRIVATE KEY BLOCK-----", "-----BEGIN PGP PUBLIC KEY BLOCK-----"),  # secret-scan: allow
        ("ssh-target","scp -r data admin@runbox.example.org:/srv", "ssh $server; ssh felix; git@github.com:a/b"),  # secret-scan: allow
        ("private-key", "-----BEGIN OPENSSH PRIVATE KEY-----", "-----BEGIN CERTIFICATE-----"),  # secret-scan: allow
    ],
)
def test_each_rule_detects_its_idiom_and_honours_the_allow_marker(rule: str, offending: str, clean: str) -> None:
    assert rule in _rules(offending), "positive control: the rule must flag the leaked idiom"
    assert rule not in _rules(clean), "negative control: the rule must not flag the accepted form"
    assert not _rules(f"{offending}  # {scanner.ALLOW_MARKER}"), "an allow-marked line is exempt"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def test_installed_hook_refuses_a_commit_that_adds_a_secret(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.org")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "core.hooksPath", str(repo / ".git" / "hooks"))
    # A common user setting that renames the diff prefixes; the scanner must not depend on them.
    _git(repo, "config", "diff.mnemonicPrefix", "true")
    password = "hunter2"
    # A secret that is already committed and not touched again must not block later commits:
    # the hook judges the change, the history question is separate.
    (repo / "legacy.txt").write_text(f"url = ftp://someone:{password}@files.example.org/a\n", encoding="utf-8")  # secret-scan: allow
    _git(repo, "add", "legacy.txt")
    _git(repo, "commit", "-q", "-m", "legacy state")

    assert scanner.main(["--install", "--repo-root", str(repo)]) == 0

    (repo / "deploy.txt").write_text(f"ok line\nurl = ftp://someone:{password}@files.example.org/b\n", encoding="utf-8")  # secret-scan: allow
    _git(repo, "add", "deploy.txt")
    capsys.readouterr()
    assert scanner.main(["--staged", "--repo-root", str(repo)]) == 1
    report = capsys.readouterr()
    assert "deploy.txt:2" in report.out + report.err and "password-url" in report.out + report.err
    assert password not in report.out + report.err, "the report must not repeat the secret (CI logs are public)"
    refused = _git(repo, "commit", "-q", "-m", "add deploy", check=False)
    assert refused.returncode != 0, "the installed pre-commit hook must refuse the commit"

    (repo / "deploy.txt").write_text("ok line\nurl = read from the environment\n", encoding="utf-8")
    _git(repo, "add", "deploy.txt")
    _git(repo, "commit", "-q", "-m", "add deploy")
    assert scanner.main(["--base", "HEAD~1", "--repo-root", str(repo)]) == 0

    # CI mode positive control: a commit that bypassed the hook is flagged on the range.
    # The first added line starts with "++ " and must not be mistaken for a file header.
    (repo / "bypass.txt").write_text(f"++ note\nurl = ftp://someone:{password}@files.example.org/c\n", encoding="utf-8")  # secret-scan: allow
    _git(repo, "add", "bypass.txt")
    _git(repo, "commit", "-q", "--no-verify", "-m", "bypass the hook")
    capsys.readouterr()
    assert scanner.main(["--base", "HEAD~1", "--repo-root", str(repo)]) == 1
    assert "bypass.txt:2 (password-url)" in capsys.readouterr().out


def test_a_diff_with_an_unrecognised_file_header_is_an_error_not_a_pass() -> None:
    # Defect: a diff layout the parser does not know (foreign prefixes) scanned as "nothing added".
    foreign = "diff --git i/x.txt w/x.txt\n--- i/x.txt\n+++ w/x.txt\n@@ -0,0 +1 @@\n+harmless\n"
    with pytest.raises(RuntimeError, match="file header"):
        scanner.scan_diff(foreign)


def test_deploy_and_verification_scripts_carry_no_server_access() -> None:
    scripts = sorted((REPO_ROOT / "scripts").glob("*.ps1"))
    assert scripts, "no PowerShell deploy scripts found; the scan would be vacuous"
    offenders = []
    for path in [*scripts, REPO_ROOT / "docs" / "verify_data.py"]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            rules = _rules(line)
            if path.suffix == ".ps1" and LITERAL_SERVER_DEFAULT.search(line):
                rules.add("literal-server-default")
            if rules:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{number} ({', '.join(sorted(rules))})")
    assert not offenders, (
        "server access data pinned in a tracked script (the deploy scripts read it from "
        f"EQASIM_SERVER_HOST / EQASIM_SERVER_USER via scripts/server_target.ps1): {offenders}"
    )
