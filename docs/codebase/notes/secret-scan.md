# Secret scan: no server access data or credentials in this public repository

This repository is public. A value that reaches a commit stays readable in the history of
every branch and in every clone, even after a later commit removes it from the files.
The only effective protection is therefore to stop the value BEFORE the commit.

## The rule

No tracked file, commit message, pull request or issue names the run server's address, an
SSH login target, a URL with an embedded password, or a private key. Scripts read the
server from the caller's environment (`Resolve-ServerTarget` in `scripts/server_target.ps1`);
prose says "the run server" or uses the SSH config alias.

## The safety net

`scripts/check_no_secrets.py` judges the lines a change ADDS. Its module docstring owns
the rule list, the exemptions (allow marker, excluded paths) and the exit codes. It runs
in two places:

- **Pre-commit hook (local, before anything is public).** Install it once per clone:
  `python scripts/check_no_secrets.py --install`. The hooks directory is shared by all
  worktrees, and the installer copies the scanner next to the hook, so the hook also
  covers branches that do not contain the scanner yet. Re-run the installer after the
  scanner changes.
- **CI step in `.github/workflows/docs.yml` (on the pull request).** It catches a commit
  made in a clone without the hook or with `git commit --no-verify`. By then the value is
  already pushed, so a CI finding means: treat the value as disclosed.

Limits, stated plainly: the scanner knows four idioms, not every possible secret (an API
token in an unknown format passes); it does not look at commit messages or pull-request
text; and a four-part version number is told apart from an address by its surroundings
(`_is_version_number`), which is a heuristic.

## Why this exists

On 2026-11-11 the owner found the run server's IP address and SSH user as parameter
defaults in the three PowerShell deploy scripts (committed 2026-06-04, copied into a run
log on 2026-06-07) and a password-bearing FTP URL in the commented-out part of the
inherited upstream `docs/verify_data.py`. The values were removed from the files; the
scanner flags the two commits that introduced the server address when replayed on them.
`tests/test_no_hardcoded_server_access.py` holds the regression tests.
