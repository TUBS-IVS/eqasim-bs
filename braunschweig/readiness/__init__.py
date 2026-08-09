"""Feature-readiness register: declared evidence per feature, mechanically checked.

The STATUS feature matrix records what exists and where; it does not record what
makes a feature trustworthy. This package holds the missing half: for each feature,
a declaration (``docs/readiness/<key>.yml``) that POINTS AT its evidence -- tests,
OFF-path byte-identity, fallback-rate instrumentation, a committed reference, the
run a KPI was measured on, and the responsible expert's assessment -- plus checks
that verify those pointers actually resolve.

The point is drift resistance. A hand-maintained evidence table degrades silently,
exactly like a silent fallback (CLAUDE.md "Fallback transparency"); a declaration
whose pointers are verified in the test suite cannot claim a test that no longer
exists, a reference that was never committed, or a flag that no longer resolves.

Design: docs/superpowers/specs/2026-08-09-feature-readiness-register-design.md
"""
from braunschweig.readiness.registry import (
    FeatureDeclaration,
    RegistryError,
    load_registry,
)
from braunschweig.readiness.checks import (
    FAIL,
    OK,
    SKIP,
    WARN,
    CheckContext,
    Finding,
    run_all_checks,
)

__all__ = [
    "FeatureDeclaration",
    "RegistryError",
    "load_registry",
    "CheckContext",
    "Finding",
    "run_all_checks",
    "OK",
    "WARN",
    "FAIL",
    "SKIP",
]
