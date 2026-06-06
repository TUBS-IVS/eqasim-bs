"""Central validation of the household-realism / IPF feature-flag combination.

The household-realism features (see CLAUDE.md, "IPF household synthesis") are
gated by several config flags with hard interdependencies. These were previously
checked by guards scattered across ``braunschweig.ipf.model`` and
``braunschweig.ipf.attributed``. This module centralises them into one pure
function so the rules live in one place and can be exercised combinatorially
(see ``braunschweig.testing.pict`` and ``tests/test_ipf_config_validation.py``).

Constraints (enabling a feature requires its prerequisite to be enabled):

  - use_household_type_margin  -> use_household_size_margin
  - use_joint_age_size_margin  -> use_household_size_margin
  - age_aware_chunking         -> use_household_size_margin
  - use_employment_margin      -> use_household_size_margin
  - sex_aware_couples          -> age_aware_chunking

Enabling the size margin is the root prerequisite because the joint / employment
margins are crossed with ``hh_size`` and the age-aware composition forms
households within each ``(commune_id, hh_size)`` bucket. Sex-aware couple pairing
only happens inside the age-aware composition pass.

The Dirichlet prior (``braunschweig.ipf.dirichlet_prior_strength``) is
intentionally NOT constrained: it is added to EVERY IPF seed cell
(``braunschweig.ipf.model``), so it is meaningful regardless of which margins are
enabled.
"""
from __future__ import annotations

from typing import Callable, Iterable, Tuple

_SIZE = "braunschweig.ipf.use_household_size_margin"

# Requirements enforced by braunschweig.ipf.model (it declares these flags in its
# configure()): the joint and employment margins are crossed with hh_size.
MODEL_REQUIREMENTS = (
    (
        "braunschweig.ipf.use_joint_age_size_margin", _SIZE,
        "the joint age x size margin is raked against the size margin",
    ),
    (
        "braunschweig.ipf.use_employment_margin", _SIZE,
        "the employment margin is crossed with hh_size",
    ),
)

# Requirements enforced by braunschweig.ipf.attributed (it declares these flags):
# household formation happens within (commune_id, hh_size) buckets, and sex-aware
# couple pairing happens inside the age-aware composition pass.
ATTRIBUTED_REQUIREMENTS = (
    (
        "braunschweig.ipf.use_household_type_margin", _SIZE,
        "hh_type is drawn within each (commune_id, hh_size) bucket",
    ),
    (
        "braunschweig.ipf.age_aware_chunking", _SIZE,
        "households are formed within each (commune_id, hh_size) bucket",
    ),
    (
        "braunschweig.chunking.sex_aware_couples",
        "braunschweig.ipf.age_aware_chunking",
        "sex-aware couple pairing happens inside the age-aware composition pass",
    ),
)

# The full constraint set. Each stage only validates the subset whose flags it
# declares in configure() (so context.config in execute() never reads an
# undeclared key); together they cover every documented interdependency.
HOUSEHOLD_REALISM_REQUIREMENTS = MODEL_REQUIREMENTS + ATTRIBUTED_REQUIREMENTS

Requirement = Tuple[str, str, str]


def validate_household_realism_config(
    get: Callable[[str], object],
    requirements: Iterable[Requirement] = HOUSEHOLD_REALISM_REQUIREMENTS,
) -> None:
    """Raise ``RuntimeError`` if a household-realism flag lacks its prerequisite.

    ``get`` is a config accessor returning the configured value for a flag key --
    a synpp ``context.config`` or a mapping's ``__getitem__`` both work.
    ``requirements`` is the subset of constraints to check (a stage passes the
    group whose flags it has declared). No-op when every enabled feature has its
    prerequisite. Fail-fast: called at the top of the IPF stages so a
    misconfiguration is reported before any expensive work runs.
    """
    for flag, prerequisite, reason in requirements:
        if bool(get(flag)) and not bool(get(prerequisite)):
            raise RuntimeError(
                f"[braunschweig.ipf.config] {flag} requires {prerequisite} "
                f"to be enabled ({reason})."
            )
