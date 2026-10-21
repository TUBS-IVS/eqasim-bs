"""In-repo vendored fork of the synpp 1.6.2 pipeline runtime (spec 2026-10-16).

``install()`` replaces, in the installed ``synpp.pipeline`` module, ``process_stages`` (ADR-0105
deterministic propagation), ``get_stage_hash`` (semantic module digest) and ``run`` (content-
addressed validity). ``run_from_yaml`` -> ``Synpp.run_pipeline`` -> ``run`` resolves ``run`` as a
module global, so the production launcher reaches the patched function. The upstream file is
pinned by SHA-256: a different installed build fails ``tests/test_synpp_fork_install.py``.
"""
from __future__ import annotations

import hashlib

import synpp.pipeline as _upstream

from braunschweig import synpp_deterministic

UnsupportedSynppVersion = synpp_deterministic.UnsupportedSynppVersion

# sha256 of the installed synpp 1.6.2 pipeline.py the fork was copied from (computed in the
# pinned eqasim env on 2026-10-16; re-check with upstream_pipeline_sha256() if the test fails).
UPSTREAM_PIPELINE_SHA256 = "cd2952b78113949b5b25078b4ec4974fe24aa754efcdd99b0c3fc3c3263068b1"

PATCHES = (
    "deterministic-propagation",   # process_stages from braunschweig.synpp_deterministic (ADR-0105)
    "semantic-module-hash",        # get_stage_hash digests the AST, not the raw text
)


def installed_synpp_version() -> str:
    return synpp_deterministic.installed_synpp_version()


def upstream_pipeline_sha256() -> str:
    with open(_upstream.__file__, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def install(*, force_version_check: bool = True) -> None:
    """Install every project patch of synpp before the stage graph is built."""
    import synpp
    synpp_deterministic.install(force_version_check=force_version_check)
    from braunschweig.synpp_fork import pipeline as fork
    _upstream.get_stage_hash = fork.get_stage_hash
    _upstream.run = fork.run
    # synpp/__init__.py re-exports run with `from .pipeline import *` at import time, so the
    # package-level name must be rebound too (tests and notebooks call synpp.run directly;
    # the launcher's run_from_yaml resolves the module global and is covered above).
    synpp.run = fork.run
