"""Model registry and documentation system for eqasim-bs.

Machine-readable registries (``docs/registry/{features,stages,data}/*.yml``),
ADR records (``docs/decisions/ADR-*.md``) and run manifests (``docs/runs/*.yml``)
are the maintained sources; human-readable views under ``docs/generated/`` are
rendered from them and never edited by hand. Deterministic checks verify that
every declared pointer resolves against the repository, the resolved canonical
production config, and the extracted synpp DAG.

Generalized from the feature-readiness register
(branch ``feature/readiness-register``, 2026-08-09), whose one-file-per-feature
declarations, strict parsing, pointer-not-copy evidence model and FAIL/WARN/SKIP
check semantics this package carries forward. Source-of-truth hierarchy and
maintenance rules: ``docs/DOCUMENTATION_GOVERNANCE.md``.

CLI::

    python -m braunschweig.documentation check    # verify every declared pointer
    python -m braunschweig.documentation build    # regenerate docs/generated/*
    python -m braunschweig.documentation dag      # re-extract synpp DAG snapshots

This package must stay importable without the scientific stack (CI runs the
metadata checks with PyYAML only); DAG extraction imports synpp lazily.
"""
from braunschweig.documentation.schema import SchemaError
from braunschweig.documentation.registries import (
    load_data,
    load_features,
    load_manifests,
    load_stages,
)

__all__ = [
    "SchemaError",
    "load_features",
    "load_stages",
    "load_data",
    "load_manifests",
]
