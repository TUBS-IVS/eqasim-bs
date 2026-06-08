"""Fail-fast configuration validation for ``population.method``.

Reads and validates the ``braunschweig.population.*`` configuration group and
returns a typed :class:`PopulationConfig`. The design follows two non-negotiable
project rules:

- **No silent fallback** (CLAUDE.md "Fallback transparency"): an unknown or
  misconfigured method raises a clear error; it never silently switches to
  another method or to the default.
- **Restricted data only when selected**: the MiD 2023 raw path is required
  *only* when ``population.method == popsim_mid``. ``simple_ipf_open`` and
  ``popsim_open`` must validate without any MiD path.

The core validator is a pure function over a flat config mapping (dotted keys,
matching the ``config:`` block of the synpp YAML) so it is trivially unit
testable. Filesystem existence checks are opt-in via ``require_paths_exist`` so
they can be exercised at execute time without coupling the unit tests to data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from braunschweig.population.methods import (
    DEFAULT_POPULATION_METHOD,
    POPULATION_METHODS,
    is_valid_method,
    requires_mid_raw,
    requires_populationsim,
)

# Canonical configuration keys (dotted, repo convention with the ``braunschweig.``
# prefix). Documented in docs/population/DATA_LAYOUT.md.
KEY_METHOD = "braunschweig.population.method"
KEY_CELLS_1KM = "braunschweig.population.popsim.cells_1km_path"
KEY_CELLS_100M = "braunschweig.population.popsim.cells_100m_path"
KEY_MID_RAW = "braunschweig.population.popsim.mid_raw_path"
KEY_OPEN_SEED = "braunschweig.population.popsim.open_seed_path"


class PopulationConfigError(ValueError):
    """Raised when the population-generation configuration is invalid.

    Carries a clear, actionable message naming the offending key and the
    accepted values / required inputs. Never swallowed; never triggers a
    fallback to another workflow.
    """


@dataclass(frozen=True)
class PopulationConfig:
    """Validated configuration for the selected population workflow.

    Attributes
    ----------
    method:
        One of ``braunschweig.population.methods.POPULATION_METHODS``.
    cells_1km_path / cells_100m_path:
        Local paths to the Zensus 2022 INSPIRE grid parquets. Required for the
        PopulationSim methods (``popsim_open`` / ``popsim_mid``); ``None`` for
        ``simple_ipf_open``.
    mid_raw_path:
        Local directory holding the restricted MiD 2023 raw CSVs. Required and
        set only for ``popsim_mid``; ``None`` otherwise.
    open_seed_path:
        Optional local path to the open seed for ``popsim_open`` (defaults to the
        existing ENTD donor when unset; resolved by the producer, not here).
    """

    method: str
    cells_1km_path: Optional[str] = None
    cells_100m_path: Optional[str] = None
    mid_raw_path: Optional[str] = None
    open_seed_path: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def uses_populationsim(self) -> bool:
        return requires_populationsim(self.method)

    @property
    def uses_mid_raw(self) -> bool:
        return requires_mid_raw(self.method)


def _get(config: Mapping[str, Any], key: str) -> Optional[str]:
    """Return a stripped string config value, or ``None`` if absent/blank."""
    value = config.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def validate_population_config(
    config: Mapping[str, Any],
    *,
    require_paths_exist: bool = False,
) -> PopulationConfig:
    """Validate the ``braunschweig.population.*`` config group, fail-fast.

    Parameters
    ----------
    config:
        Flat mapping of dotted config keys (the synpp ``config:`` block).
    require_paths_exist:
        When ``True``, additionally assert that the required input paths exist
        on disk (used at execute time). Defaults to ``False`` so unit tests stay
        pure.

    Returns
    -------
    PopulationConfig
        The validated, typed configuration.

    Raises
    ------
    PopulationConfigError
        If the method is unknown, or a required input for the selected method is
        missing. Never falls back to another method.
    """
    # 1) Method: explicit default, but a PRESENT-yet-invalid value is an error
    #    (no silent fallback to the default).
    if KEY_METHOD not in config or _get(config, KEY_METHOD) is None:
        method = DEFAULT_POPULATION_METHOD
    else:
        method = _get(config, KEY_METHOD)
        if not is_valid_method(method):
            raise PopulationConfigError(
                f"{KEY_METHOD} = {method!r} is not a valid population method. "
                f"Choose one of {list(POPULATION_METHODS)}. "
                "The pipeline will not fall back to another workflow."
            )

    cells_1km = _get(config, KEY_CELLS_1KM)
    cells_100m = _get(config, KEY_CELLS_100M)
    mid_raw = _get(config, KEY_MID_RAW)
    open_seed = _get(config, KEY_OPEN_SEED)

    # 2) PopulationSim methods require the spatial cell parquets.
    if requires_populationsim(method):
        missing = [
            key
            for key, val in ((KEY_CELLS_1KM, cells_1km), (KEY_CELLS_100M, cells_100m))
            if val is None
        ]
        if missing:
            raise PopulationConfigError(
                f"population.method = {method!r} requires the Zensus grid paths "
                f"{missing}. Set them in the config (see "
                "docs/population/DATA_LAYOUT.md)."
            )

    # 3) MiD raw is required ONLY for popsim_mid, and must NOT be silently
    #    sourced for the open workflows.
    if requires_mid_raw(method):
        if mid_raw is None:
            raise PopulationConfigError(
                f"population.method = {method!r} requires the restricted MiD 2023 "
                f"raw path {KEY_MID_RAW!r}. Provide it via config / environment / a "
                "local ignored config file (never committed). For an open run use "
                "population.method = 'popsim_open' or 'simple_ipf_open'."
            )
    else:
        # Keep the open workflows provably MiD-free: ignore (do not require) any
        # MiD path; carry it as None so no downstream stage can read it.
        mid_raw = None

    # 4) Optional on-disk existence checks (execute time).
    if require_paths_exist:
        _check_exists(method, cells_1km, cells_100m, mid_raw)

    return PopulationConfig(
        method=method,
        cells_1km_path=cells_1km,
        cells_100m_path=cells_100m,
        mid_raw_path=mid_raw,
        open_seed_path=open_seed,
        raw=dict(config),
    )


def _check_exists(
    method: str,
    cells_1km: Optional[str],
    cells_100m: Optional[str],
    mid_raw: Optional[str],
) -> None:
    """Assert required inputs for ``method`` exist on disk; fail-fast otherwise."""
    problems: list[str] = []
    if requires_populationsim(method):
        for label, value in (("cells_1km_path", cells_1km), ("cells_100m_path", cells_100m)):
            if value is not None and not Path(value).is_file():
                problems.append(f"{label} does not exist: {value}")
    if requires_mid_raw(method) and mid_raw is not None and not Path(mid_raw).is_dir():
        problems.append(f"mid_raw_path is not a directory: {mid_raw}")
    if problems:
        raise PopulationConfigError(
            "Missing population inputs for method "
            f"{method!r}:\n  - " + "\n  - ".join(problems)
        )


def validate_from_context(context) -> PopulationConfig:
    """Read + validate the population config from a synpp ``context``.

    Helper for the synpp stage layer. Reads each known key via
    ``context.config(key, default)`` into a flat dict and delegates to
    :func:`validate_population_config`.
    """
    flat = {
        KEY_METHOD: context.config(KEY_METHOD, DEFAULT_POPULATION_METHOD),
        KEY_CELLS_1KM: context.config(KEY_CELLS_1KM, None),
        KEY_CELLS_100M: context.config(KEY_CELLS_100M, None),
        KEY_MID_RAW: context.config(KEY_MID_RAW, None),
        KEY_OPEN_SEED: context.config(KEY_OPEN_SEED, None),
    }
    return validate_population_config(flat)
