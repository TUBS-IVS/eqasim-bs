"""Config-key migration helper.

Phase 2.6 of the eqasim-bs refactor renamed several synpp config keys from
``bavaria.ipf.*`` to ``braunschweig.ipf.*``.  This module exposes a one-shot
migration helper that warns when a legacy key is encountered and remaps it to
the new name.  It is dropped in Phase 4.3.
"""

from __future__ import annotations

import warnings
from typing import Mapping, MutableMapping


# Keys renamed in Phase 2.6 - mapping legacy -> current.
LEGACY_KEY_MAP: Mapping[str, str] = {
    "bavaria.ipf.use_household_size_margin":
        "braunschweig.ipf.use_household_size_margin",
    "bavaria.ipf.use_household_type_margin":
        "braunschweig.ipf.use_household_type_margin",
    "bavaria.ipf.use_employment_margin":
        "braunschweig.ipf.use_employment_margin",
    "bavaria.ipf.dirichlet_prior_strength":
        "braunschweig.ipf.dirichlet_prior_strength",
    "bavaria.ipf.margin_validation_tolerance":
        "braunschweig.ipf.margin_validation_tolerance",
    "bavaria.ipf.max_iterations":
        "braunschweig.ipf.max_iterations",
    "bavaria.ipf.tolerance":
        "braunschweig.ipf.tolerance",
    "bavaria.ipf.employment_by_hhsize_path":
        "braunschweig.ipf.employment_by_hhsize_path",
}


def migrate_legacy_keys(config: MutableMapping[str, object]) -> MutableMapping[str, object]:
    """Rewrite legacy ``bavaria.ipf.*`` keys to ``braunschweig.ipf.*`` in-place.

    Emits a :class:`DeprecationWarning` for every legacy key encountered.
    Existing entries under the new name take precedence (the legacy value is
    discarded after a warning).  Returns the mutated mapping for chaining.
    """
    for legacy, current in LEGACY_KEY_MAP.items():
        if legacy not in config:
            continue
        legacy_value = config.pop(legacy)
        if current in config:
            warnings.warn(
                f"Config key '{legacy}' is deprecated and was ignored because "
                f"'{current}' is already set.",
                DeprecationWarning,
                stacklevel=2,
            )
            continue
        warnings.warn(
            f"Config key '{legacy}' is deprecated; use '{current}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        config[current] = legacy_value
    return config
