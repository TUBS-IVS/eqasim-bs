"""Refuse a change that adds server access data or credentials to this public repository.

    python scripts/check_no_secrets.py --install            # install the pre-commit hook (once per clone)
    python scripts/check_no_secrets.py --staged             # what the hook runs: scan the staged change
    python scripts/check_no_secrets.py --base origin/main   # CI: scan what HEAD adds since the merge base
    python scripts/check_no_secrets.py --tree               # report-only inventory of all tracked files

Only ADDED lines are judged, so a commit is never blocked by what is already in the
repository. Rules (``RULES`` below): a URL with an embedded password, a private-key
header, a publicly routable IPv4 address, and a remote-shell or file-copy command
(``_SSH_COMMAND``) with a literal login target (user, at sign, host). Loopback, private and documentation
addresses, shell variables and ``<placeholders>`` are accepted.

A line that must legitimately carry such a value (a four-part version number that parses
as an address, a synthetic test input) ends with the marker ``secret-scan: allow``; files
matching ``EXCLUDED_PATHS`` (lock files, vector graphics) are not scanned. Findings are
reported as ``path:line (rule)`` without the line's content, because CI logs are public.

The hook is a local safety net: it does not run in a clone where it was not installed
and ``git commit --no-verify`` skips it. The CI step catches such a change on the pull
request, i.e. after it was pushed. Background: ``docs/codebase/notes/secret-scan.md``.

Exit codes: 0 no finding (``--tree`` always, it only reports); 1 a finding or a git error.
"""
from __future__ import annotations

import argparse
import fnmatch
import ipaddress
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ALLOW_MARKER = "secret-scan: allow"

# Generated or binary-like files whose dotted version numbers and coordinates would only
# produce noise. Kept short on purpose: every entry is a place the scanner does not see.
EXCLUDED_PATHS = ("environments/*", "*.lock", "*.svg")

HOOK_SOURCE = Path(__file__).resolve().parent / "git-hooks" / "pre-commit"
HOOK_SIGNATURE = "eqasim-bs secret scan"

_PASSWORD_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@\"']+:[^/\s@\"']+@", re.IGNORECASE)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")  # " BLOCK": OpenPGP
# Exactly four dot-separated groups. A dot directly after the quad is allowed when no digit
# follows it, so an address that ends a sentence is still seen.
_IPV4 = re.compile(r"(?<!\d)(?<!\d\.)(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
_SSH_COMMAND = re.compile(r"\b(?:ssh|scp|sftp|rsync)\b", re.IGNORECASE)
_LOGIN_TARGET = re.compile(r"(?<![\w.$-])([A-Za-z_][\w.-]*)@[A-Za-z0-9][\w.-]*")


class Finding(NamedTuple):
    rule: str
    path: str = ""
    line_number: int = 0


# A four-part version number (JDK 25.0.4.1, kernel Linux-5.15.153.1) parses as a public
# address. It is recognised by what surrounds it: glued to a name ("jdk-25.0.4.101-hotspot",
# "Temurin-25.0.4.1+1") or directly announced by a version word ("JDK 25.0.4.1",
# 'version "25.0.4.1"', "(build 25.0.4.1"). Nothing may stand between the word and the
# number except one separator and a quote: "the build server <address>" is not exempt.
# Measured on the tracked tree of 2026-11-11: 36 of the 37 dotted quads in docs/runs/ are
# recognised this way; the one left starts a wrapped line and would need the allow marker.
_VERSION_GLUE_BEFORE = re.compile(r"[A-Za-z_-]$")
_VERSION_GLUE_AFTER = re.compile(r"^[A-Za-z_+-]")
_VERSION_WORD_BEFORE = re.compile(r"\b(?:jdk|jre|java|temurin|version|build)[:=]?\s?[\"']?v?$", re.IGNORECASE)


def _is_version_number(line: str, start: int, end: int) -> bool:
    before, after = line[:start], line[end:]
    return bool(_VERSION_GLUE_BEFORE.search(before) or _VERSION_GLUE_AFTER.search(after)
                or _VERSION_WORD_BEFORE.search(before))


def _has_public_ipv4(line: str) -> bool:
    for match in _IPV4.finditer(line):
        if _is_version_number(line, match.start(), match.end()):
            continue
        try:
            address = ipaddress.ip_address(match.group(0))
        except ValueError:  # an octet above 255: a version number, not an address
            continue
        if address.is_global:
            return True
    return False


def _has_ssh_login_target(line: str) -> bool:
    if not _SSH_COMMAND.search(line):
        return False
    return any(match.group(1) != "git" for match in _LOGIN_TARGET.finditer(line))


RULES = {
    "password-url": lambda line: bool(_PASSWORD_URL.search(line)),
    "private-key": lambda line: bool(_PRIVATE_KEY.search(line)),
    "public-ipv4": _has_public_ipv4,
    "ssh-target": _has_ssh_login_target,
}


def scan_line(line: str) -> list:
    """Findings for one line of text; an allow-marked line has none."""
    if ALLOW_MARKER in line:
        return []
    return [Finding(rule) for rule, matches in RULES.items() if matches(line)]


def _is_excluded(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in EXCLUDED_PATHS)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=repo, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")


def scan_diff(diff_text: str) -> list:
    """Findings on the added lines of a unified diff produced with ``_DIFF_OPTIONS``.

    A file's header lines end at its first hunk, so an added content line that itself
    starts with "++ " is content, not a header. A hunk without a recognised ``+++ b/<path>``
    header raises: scanning nothing must never look like a clean change.
    """
    findings, path, line_number, in_header = [], None, 0, True
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            path, in_header = None, True
            continue
        hunk = _HUNK.match(line)
        if hunk:
            if path is None:
                raise RuntimeError("the diff has a hunk without a recognised '+++ b/<path>' file header; "
                                   "the change cannot be scanned")
            line_number, in_header = int(hunk.group(1)), False
            continue
        if in_header:
            if line.startswith("+++ b/"):
                path = line[6:]
            continue
        if not line.startswith("+"):
            continue
        if not _is_excluded(path):
            findings.extend(Finding(f.rule, path, line_number) for f in scan_line(line[1:]))
        line_number += 1
    return findings


# Fixed prefixes and no external diff or textconv driver: the output must not depend on the
# user's git configuration (diff.mnemonicPrefix and diff.noprefix rename the prefixes).
_DIFF_OPTIONS = ("-U0", "--no-color", "--no-renames", "--diff-filter=AM", "--src-prefix=a/", "--dst-prefix=b/",
                 "--no-ext-diff", "--no-textconv")


def scan_staged(repo: Path) -> list:
    return scan_diff(_git(repo, "diff", "--cached", *_DIFF_OPTIONS))


def scan_range(repo: Path, base: str, head: str = "HEAD") -> list:
    merge_base = _git(repo, "merge-base", base, head).strip()
    return scan_diff(_git(repo, "diff", *_DIFF_OPTIONS, merge_base, head))


def scan_tree(repo: Path) -> list:
    findings = []
    for path in _git(repo, "ls-files").splitlines():
        if _is_excluded(path):
            continue
        try:
            text = (repo / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # binary or absent (sparse checkout): nothing to read
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            findings.extend(Finding(f.rule, path, number) for f in scan_line(line))
    return findings


def install_hook(repo: Path) -> Path:
    """Copy the hook and this scanner into the repository's hooks directory.

    The scanner is copied next to the hook so that the hook also works on branches that
    do not contain this file yet (the hooks directory is shared by all worktrees).
    """
    hooks_directory = Path(_git(repo, "rev-parse", "--git-path", "hooks").strip())
    if not hooks_directory.is_absolute():
        hooks_directory = repo / hooks_directory
    hooks_directory.mkdir(parents=True, exist_ok=True)
    target = hooks_directory / "pre-commit"
    if target.exists() and HOOK_SIGNATURE not in target.read_text(encoding="utf-8", errors="replace"):
        raise RuntimeError(f"{target} is a different pre-commit hook; add a call to "
                           f"'python scripts/check_no_secrets.py --staged' to it by hand")
    target.write_bytes(HOOK_SOURCE.read_bytes().replace(b"\r\n", b"\n"))
    target.chmod(0o755)
    shutil.copyfile(Path(__file__).resolve(), hooks_directory / "check_no_secrets.py")
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="scan the lines the staged change adds")
    mode.add_argument("--base", help="scan the lines --head adds since its merge base with this ref")
    mode.add_argument("--tree", action="store_true", help="report findings in all tracked files (exit 0)")
    mode.add_argument("--install", action="store_true", help="install the pre-commit hook into this clone")
    parser.add_argument("--head", default="HEAD", help="the commit to check with --base (default: HEAD)")
    parser.add_argument("--repo-root", default=None, help="repository to operate on (default: current directory)")
    args = parser.parse_args(argv)
    repo = Path(args.repo_root) if args.repo_root else Path.cwd()
    try:
        if args.install:
            print(f"[secret-scan] installed {install_hook(repo)}")
            return 0
        if args.staged:
            findings = scan_staged(repo)
        elif args.base:
            findings = scan_range(repo, args.base, args.head)
        else:
            findings = scan_tree(repo)
    except RuntimeError as error:
        print(f"[secret-scan] error: {error}", file=sys.stderr)
        return 1
    for finding in findings:
        print(f"[secret-scan] {finding.path}:{finding.line_number} ({finding.rule})")
    if args.tree:
        print(f"[secret-scan] inventory: {len(findings)} finding(s) in tracked files (report only)")
        return 0
    if not findings:
        print("[secret-scan] OK: the change adds no server address, login target, password URL or private key")
        return 0
    print(f"[secret-scan] FAIL: {len(findings)} added line(s) carry server access data or a credential. "
          f"This repository is public: read the value from the environment instead "
          f"(scripts/server_target.ps1), or end a harmless line with '{ALLOW_MARKER}'.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
