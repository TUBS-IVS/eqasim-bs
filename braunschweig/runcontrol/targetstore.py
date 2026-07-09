"""Persistence + validation for dynamically-added ssh execution targets (Task 14).

Config-file targets declared in `runcontrol.toml` (including the pre-seeded `server`
= felix) are immutable seeds, loaded once by `settings.load_settings` and never
written back. Targets added at runtime through the web form (`POST /api/targets`)
live in a separate JSON file under the gitignored data dir instead, so they survive
a process restart without polluting the committed config. On a name collision
between a dynamic target and a config target, the config target always wins: the
caller (see `__main__.cmd_serve`) is expected to log a warning and drop the
colliding dynamic entry rather than let it shadow a curated target.

Only `kind == "ssh"` targets are creatable dynamically -- a "local" target implies
code executing on this machine's filesystem, which is a config-time decision, not
something a remote user should be able to grant themselves through the web form.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .settings import TargetConfig

logger = logging.getLogger(__name__)

# Names are used as dict keys and in URLs/log lines only; kept short and shell-safe.
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# host ends up as a literal ssh argv element (see targets.ssh.SshTarget), so the
# character class excludes whitespace and everything an ssh/getopt option string
# could use; the leading-'-' check below additionally blocks "-oProxyCommand=..." style
# ssh option injection even though such an option string could technically match this class.
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")

# Fixed for all dynamically-added targets; matches the [target.server] example in
# runcontrol.toml. Kept as a module constant so app.py does not duplicate the literal.
DEFAULT_SSH_RUNNER = "scripts/run_pipeline.sh"


def load_dynamic_targets(path: Path) -> dict[str, TargetConfig]:
    """Load previously-added dynamic targets; {} (not an error) when the file is absent.

    A missing store simply means no dynamic target has ever been added on this
    machine -- the config-file targets loaded by load_settings() remain fully usable.
    """
    path = Path(path)
    if not path.exists():
        logger.info("no dynamic target store at %s (no user-added targets yet)", path)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    targets: dict[str, TargetConfig] = {}
    for name, t in raw.get("targets", {}).items():
        targets[name] = TargetConfig(
            name=name,
            kind=t.get("kind", "ssh"),
            repo=t["repo"],
            host=t.get("host"),
            runner=t.get("runner", DEFAULT_SSH_RUNNER),
            data_dir=t.get("data_dir", "eqasim-data"),
            logs_dir=t.get("logs_dir", "logs"),
        )
    logger.info("loaded %d dynamic target(s) from %s", len(targets), path)
    return targets


def save_dynamic_targets(path: Path, targets: dict[str, TargetConfig]) -> None:
    """Overwrite the dynamic target store with exactly the given targets."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "targets": {
            name: {
                "kind": cfg.kind,
                "host": cfg.host,
                "repo": cfg.repo,
                "runner": cfg.runner,
                "data_dir": cfg.data_dir,
                "logs_dir": cfg.logs_dir,
            }
            for name, cfg in targets.items()
        }
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    logger.info("saved %d dynamic target(s) to %s", len(targets), path)


def validate_new_target(name: str, host: str, repo: str, existing: set[str]) -> None:
    """Reject anything unsafe or colliding before a TargetConfig is ever built.

    host and repo are interpolated straight into an ssh argv / remote shell command
    (see targets.ssh.SshTarget), so this is a security boundary, not just a UX
    convenience: reject option-injection attempts (leading '-') and anything that
    could smuggle extra shell tokens (whitespace, newlines) in host or repo.
    """
    if not _NAME_PATTERN.match(name):
        raise ValueError(f"target name '{name}' is invalid; must match ^[A-Za-z0-9_-]{{1,32}}$")
    if name in existing:
        raise ValueError(f"target name '{name}' is already defined in runcontrol.toml or already added; choose another name")
    if not host or host.startswith("-") or any(ch.isspace() for ch in host) or not _HOST_PATTERN.match(host):
        raise ValueError(f"host '{host}' is invalid; must be an ssh alias, IP, or user@host with no whitespace, "
                         "and must not start with '-' (ssh option injection)")
    if not repo or repo.startswith("-") or "\n" in repo or "\r" in repo:
        raise ValueError(f"repo '{repo}' is invalid; must be a non-empty path, must not start with '-', "
                         "and must not contain a newline")
