"""Assemble a PopulationSim run folder from the cell grid, controls and seed.

A PopulationSim run reads a self-contained folder:

    <folder>/
      data/    geo_cross_walk.csv, control_totals_<geo>.csv, seed_households.csv,
               seed_persons.csv
      configs/ controls.csv, settings.yaml, logging.yaml
      output/  (created empty; PopulationSim writes here)

This module builds the deterministic, schema-agnostic pieces of that folder: the
geography crosswalk (WELT > STAAT > ZENSUS1km > ZENSUS100m), the
hierarchically-consistent control totals (100 m integerized within each 1 km
parent via the largest-remainder rule, 1 km = the exact sum of its children,
STAAT/WELT = national totals), and the folder writer.

The mapping from the Zensus cell parquet's (German-named) binned columns to the
control targets is intentionally NOT done here: callers pass a per-cell target
table whose columns are already the control target names. That binding depends on
the real cell parquet schema and is a separate, data-specific concern.

Replaces the in-notebook folder generation of popsimprep's Step 2/3.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from pathlib import Path
from typing import Iterable, Mapping, Sequence, Union

import yaml

import numpy as np
import pandas as pd

from braunschweig.popsim import cells, controls

logger = logging.getLogger(__name__)

# Geography level names, top (coarsest) to bottom (finest). Matches the
# PopulationSim settings.yaml geographies for this workflow.
GEO_WELT = "WELT"
GEO_STAAT = "STAAT"
GEO_KREIS = "KREIS"  # Tier-3 control geography (employment/education); ARS[:5]
GEO_1KM = "ZENSUS1km"
GEO_100M = "ZENSUS100m"

CONTROL_GEOGRAPHIES: Sequence[str] = (GEO_100M, GEO_1KM, GEO_STAAT, GEO_WELT)


def validate_settings_geographies(settings_yaml: str, controls_df) -> None:
    """Fail fast when the PopulationSim settings do not declare every control geography.

    The settings file is a LOCAL-ONLY path (it lives in the popsimprep repo, not here) and
    is copied into every batch folder as raw text, so nothing used to relate it to the
    control set this run actually renders. PopulationSim itself DOES catch the mismatch --
    ``populationsim.steps.setup_data_structures`` raises ``RuntimeError("unknown geography
    column '<geo>' in control file")`` -- so this check does not prevent a silently wrong
    population; a mismatched pair always crashed. What it changes is WHEN and HOW: the
    failure moves from late inside a worker subprocess, after every batch folder has been
    written, with a message naming neither the settings file nor the fix, to here, before
    any of that work happens, with a message that names both.

    The concrete case this guards: the per-Kreis attribute controls
    (``kreis_attribute_control.REGISTRY``) default ON and render at :data:`GEO_KREIS`, but
    the 4-level settings file (``WELT > STAAT > ZENSUS1km > ZENSUS100m``) has no KREIS
    level, so every one of them would be unconstrained.

    Parameters
    ----------
    settings_yaml:
        Raw text of the PopulationSim ``settings.yaml`` that will be written into each
        batch folder.
    controls_df:
        The rendered ``controls.csv`` frame; its ``geography`` column names the level each
        control is balanced at.

    Raises
    ------
    ValueError
        If the settings declare no ``geographies`` list, or if any control geography is
        missing from it. The message names the missing level(s), how many controls sit
        there, and what the settings do declare, so the reader can pick the right file.
    KeyError
        If ``controls_df`` has no ``geography`` column (a caller passing the wrong frame
        must not be read as "nothing to check").
    """
    if "geography" not in getattr(controls_df, "columns", ()):
        raise KeyError(
            "validate_settings_geographies: controls frame has no 'geography' column; "
            "cannot check it against the PopulationSim settings.")
    settings = yaml.safe_load(settings_yaml) or {}
    declared = settings.get("geographies") if isinstance(settings, Mapping) else None
    if not declared:
        raise ValueError(
            "PopulationSim settings declare no 'geographies' list; cannot verify that the "
            "control set is balanced at levels the run knows about.")
    declared_set = set(declared)
    counts = controls_df["geography"].value_counts()
    missing = {geography: int(n) for geography, n in counts.items()
               if geography not in declared_set}
    if missing:
        detail = ", ".join(f"{geography} ({n} control(s))"
                           for geography, n in sorted(missing.items()))
        raise ValueError(
            f"PopulationSim settings do not declare the control geography/-ies {detail}; "
            f"they declare {list(declared)}. Those controls would not be balanced. Point "
            "braunschweig.population.popsim.settings_path at a settings file whose "
            "'geographies' list contains them (the KREIS level is required whenever any "
            "per-Kreis attribute control or tier3 is active -- all of them default ON).")


# Name of PopulationSim's diagnostic step that writes output/data_dict.csv and
# output/data_dict.txt (a column dictionary of the pipeline tables). No code in this
# repository reads those files.
DATA_DICTIONARY_STEP = "write_data_dictionary"

_DATA_DICTIONARY_LINE = re.compile(
    r"^[ \t]*-[ \t]*" + re.escape(DATA_DICTIONARY_STEP) + r"[ \t]*(#[^\r\n]*)?\r?\n?$")


def drop_data_dictionary_step(settings_yaml: str) -> str:
    """Remove the ``write_data_dictionary`` step from the ``models:`` list of a settings text.

    The removal is textual: exactly one block-list line ``- write_data_dictionary`` is cut
    and every other byte of the settings text stays as it was (comments, key order and
    quoting included), so the batch ``settings.yaml`` differs from the server-local source
    file by that one line only. The result is then parsed and checked: its ``models`` list
    must equal the original list without the step, and every other top-level key must be
    unchanged.

    Parameters
    ----------
    settings_yaml:
        Raw text of the PopulationSim ``settings.yaml`` (the file named by
        ``braunschweig.population.popsim.settings_path``).

    Returns
    -------
    str
        The settings text without the step's line.

    Raises
    ------
    ValueError
        If ``models`` does not list the step exactly once, if the step is not written as
        exactly one block-list line (e.g. a flow list ``models: [a, b]``), or if the
        textual removal changed anything besides that one list entry. Dropping is asked
        for explicitly, so a settings file that does not match is a configuration error,
        not something to skip quietly.
    """
    original = yaml.safe_load(settings_yaml) or {}
    models = original.get("models") if isinstance(original, Mapping) else None
    if not isinstance(models, list) or models.count(DATA_DICTIONARY_STEP) != 1:
        raise ValueError(
            f"Cannot drop the PopulationSim step '{DATA_DICTIONARY_STEP}': the settings "
            f"'models' list must contain it exactly once, got {models!r}. Set "
            "braunschweig.population.popsim.write_data_dictionary to true, or point "
            "braunschweig.population.popsim.settings_path at a settings file that runs "
            "the step.")
    lines = settings_yaml.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if _DATA_DICTIONARY_LINE.match(line)]
    if len(hits) != 1:
        raise ValueError(
            f"Cannot drop the PopulationSim step '{DATA_DICTIONARY_STEP}': expected exactly "
            f"one block-list line '- {DATA_DICTIONARY_STEP}' in the settings text, found "
            f"{len(hits)}. Write 'models:' as a block list (one '- step' per line).")
    result = "".join(lines[:hits[0]] + lines[hits[0] + 1:])
    parsed = yaml.safe_load(result) or {}
    expected = dict(original)
    expected["models"] = [step for step in models if step != DATA_DICTIONARY_STEP]
    if parsed != expected:
        raise ValueError(
            f"Removing the line '- {DATA_DICTIONARY_STEP}' changed more than the 'models' "
            "list entry of the PopulationSim settings; refusing to write altered settings.")
    return result


# PopulationSim settings that braunschweig.population.popsim.* config keys decide (the
# group's PopulationSim fork, version 0.10.0+bs.1; each defaults to upstream behaviour
# inside the fork). Meaning of each: the matching KEY_* comment in
# braunschweig.popsim.stage.config_keys.
KEEP_FRACTIONAL_SETTING = "KEEP_FRACTIONAL_SAMPLE_WEIGHTS"
INTEGERIZER_DRAW_SETTING = "INTEGERIZER_DRAW"
INTEGERIZER_TIMEOUT_SETTING = "INTEGERIZER_TIMEOUT"
# The name PopulationSim 0.10.0 used for the timeout; the fork still reads it and raises
# when both names carry different values.
INTEGERIZER_TIMEOUT_ALIAS = "INTEGIZER_TIMEOUT"
INTEGERIZER_PATH_RECORD_SETTING = "INTEGERIZER_PATH_RECORD"
INTEGERIZER_SEED_SETTING = "INTEGERIZER_RANDOM_SEED"
INTEGERIZER_DRAWS = ("off", "pareto", "gumbel")
# Controlled rounding (fork release 0.10.0+bs.2, issue #646); meaning of each: the matching
# KEY_INTEGERIZER_* comments in config_keys.
INTEGERIZER_ROUNDING_SETTING = "INTEGERIZER_ROUNDING"
INTEGERIZER_PENALTY_FLOOR_SETTING = "INTEGERIZER_PENALTY_FLOOR"
INTEGERIZER_PENALTY_CAP_SETTING = "INTEGERIZER_PENALTY_CAP"
INTEGERIZER_PARENT_TOTAL_FACTOR_SETTING = "INTEGERIZER_PARENT_TOTAL_FACTOR"
INTEGERIZER_CONSERVE_PENALTY_SETTING = "INTEGERIZER_CONSERVE_PENALTY"
INTEGERIZER_CONTROLLED_METHOD_SETTING = "INTEGERIZER_CONTROLLED_METHOD"
INTEGERIZER_SEARCH_MAX_SWEEPS_SETTING = "INTEGERIZER_SEARCH_MAX_SWEEPS"
INTEGERIZER_SEARCH_SHORTLIST_SETTING = "INTEGERIZER_SEARCH_SHORTLIST"
INTEGERIZER_ROUNDINGS = ("round", "controlled")
INTEGERIZER_CONTROLLED_METHODS = ("search", "lp")
_MAX_INTEGERIZER_SEED = 2 ** 32


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_known_draw(value: object) -> bool:
    return isinstance(value, str) and value in INTEGERIZER_DRAWS


def _is_known_rounding(value: object) -> bool:
    return isinstance(value, str) and value in INTEGERIZER_ROUNDINGS


def _is_known_controlled_method(value: object) -> bool:
    return isinstance(value, str) and value in INTEGERIZER_CONTROLLED_METHODS


def _is_finite_number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _is_positive_number(value: object) -> bool:
    return _is_finite_number(value) and value > 0


def _is_factor_of_at_least_one(value: object) -> bool:
    return _is_finite_number(value) and value >= 1


def _is_non_negative_number(value: object) -> bool:
    return _is_finite_number(value) and value >= 0


# setting name -> (validator, what a valid value is)
_OVERRIDE_RULES = {
    KEEP_FRACTIONAL_SETTING: (_is_bool, "a boolean"),
    INTEGERIZER_DRAW_SETTING: (_is_known_draw, f"one of {list(INTEGERIZER_DRAWS)}"),
    INTEGERIZER_TIMEOUT_SETTING: (_is_positive_int, "a positive integer (seconds)"),
    INTEGERIZER_PATH_RECORD_SETTING: (_is_bool, "a boolean"),
    INTEGERIZER_ROUNDING_SETTING: (_is_known_rounding, f"one of {list(INTEGERIZER_ROUNDINGS)}"),
    INTEGERIZER_PENALTY_FLOOR_SETTING: (_is_positive_number, "a finite number > 0"),
    INTEGERIZER_PENALTY_CAP_SETTING: (_is_finite_number, "a finite number"),
    INTEGERIZER_PARENT_TOTAL_FACTOR_SETTING: (
        _is_factor_of_at_least_one, "a finite number >= 1"),
    INTEGERIZER_CONSERVE_PENALTY_SETTING: (_is_non_negative_number, "a finite number >= 0"),
    INTEGERIZER_CONTROLLED_METHOD_SETTING: (
        _is_known_controlled_method, f"one of {list(INTEGERIZER_CONTROLLED_METHODS)}"),
    INTEGERIZER_SEARCH_MAX_SWEEPS_SETTING: (_is_positive_int, "a positive integer (sweeps)"),
    INTEGERIZER_SEARCH_SHORTLIST_SETTING: (_is_positive_int, "an integer >= 1"),
}


def _same_value(left: object, right: object) -> bool:
    """Equality that lets neither ``True == 1`` nor ``False`` (bare ``off``) vs ``'off'`` pass."""
    return type(left) is type(right) and left == right


def populationsim_overrides_from_config(
    *, keep_fractional_sample_weights: object, integerizer_draw: object,
    integerizer_timeout_s: object, integerizer_rounding: object,
    integerizer_penalty_floor: object, integerizer_penalty_cap: object,
    integerizer_parent_total_factor: object, integerizer_conserve_penalty: object,
    integerizer_controlled_method: object, integerizer_search_max_sweeps: object,
    integerizer_search_shortlist: object,
) -> dict[str, object]:
    """Build the PopulationSim settings that the tracked config decides, validated.

    ``INTEGERIZER_PATH_RECORD`` is always ``true``: the per-call path record is the
    evidence the integerizer gate of the batch check reads. The controlled-rounding
    penalty values, the controlled method, the two search options and the rounding mode are
    always written too, also with the neutral ``round``. Meaning of the configurable values:
    the matching ``KEY_*`` comments in ``braunschweig.popsim.stage.config_keys``.

    Raises
    ------
    ValueError
        If a value is not valid for its setting, or the penalty cap is below the penalty
        floor (names the setting).
    """
    overrides = {
        KEEP_FRACTIONAL_SETTING: keep_fractional_sample_weights,
        INTEGERIZER_DRAW_SETTING: integerizer_draw,
        INTEGERIZER_TIMEOUT_SETTING: integerizer_timeout_s,
        INTEGERIZER_PATH_RECORD_SETTING: True,
        INTEGERIZER_ROUNDING_SETTING: integerizer_rounding,
        INTEGERIZER_PENALTY_FLOOR_SETTING: integerizer_penalty_floor,
        INTEGERIZER_PENALTY_CAP_SETTING: integerizer_penalty_cap,
        INTEGERIZER_PARENT_TOTAL_FACTOR_SETTING: integerizer_parent_total_factor,
        INTEGERIZER_CONSERVE_PENALTY_SETTING: integerizer_conserve_penalty,
        INTEGERIZER_CONTROLLED_METHOD_SETTING: integerizer_controlled_method,
        INTEGERIZER_SEARCH_MAX_SWEEPS_SETTING: integerizer_search_max_sweeps,
        INTEGERIZER_SEARCH_SHORTLIST_SETTING: integerizer_search_shortlist,
    }
    _validate_overrides(overrides)
    if integerizer_penalty_cap < integerizer_penalty_floor:
        raise ValueError(
            f"PopulationSim setting {INTEGERIZER_PENALTY_CAP_SETTING} must be >= "
            f"{INTEGERIZER_PENALTY_FLOOR_SETTING}, got cap {integerizer_penalty_cap!r} < "
            f"floor {integerizer_penalty_floor!r}.")
    return overrides


def _validate_overrides(overrides: Mapping[str, object]) -> None:
    for key, value in overrides.items():
        if key not in _OVERRIDE_RULES:
            raise ValueError(
                f"Unknown PopulationSim setting override {key!r}; known: "
                f"{sorted(_OVERRIDE_RULES)}.")
        is_valid, expected = _OVERRIDE_RULES[key]
        if not is_valid(value):
            raise ValueError(
                f"PopulationSim setting {key} must be {expected}, got {value!r}.")


def _format_setting_value(value: object) -> str:
    """YAML text of one validated override value (strings quoted: bare ``off`` is False)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f"'{value}'"
    return str(value)


def _line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _append_settings_lines(settings_yaml: str, lines: Sequence[str]) -> str:
    eol = _line_ending(settings_yaml)
    separator = "" if (not settings_yaml or settings_yaml.endswith("\n")) else eol
    return settings_yaml + separator + "".join(line + eol for line in lines)


def _parse_settings_mapping(settings_yaml: str) -> dict:
    parsed = yaml.safe_load(settings_yaml)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError("The PopulationSim settings text is not a YAML mapping.")
    return parsed


def apply_populationsim_overrides(
    settings_yaml: str, overrides: Mapping[str, object],
) -> str:
    """Append the tracked PopulationSim settings to a settings text.

    One ``KEY: value`` line per override is added at the end, under one comment line;
    every byte of the source text stays as it was. A key the raw text already sets to the
    same value is not repeated. The result is parsed again and must equal the source
    mapping plus the overrides. One INFO line names the applied overrides.

    Raises
    ------
    ValueError
        If a value is invalid for its setting; if the raw text already sets an override
        key, or the alias ``INTEGIZER_TIMEOUT`` for the timeout, to another value (the
        error names the key: the tracked config would silently lose against the
        server-local file otherwise); or if the textual append changed any other key.
    """
    _validate_overrides(overrides)
    original = _parse_settings_mapping(settings_yaml)
    pending: list[str] = []
    for key, value in overrides.items():
        names = [key]
        if key == INTEGERIZER_TIMEOUT_SETTING:
            names.append(INTEGERIZER_TIMEOUT_ALIAS)
        for name in names:
            if name in original and not _same_value(original[name], value):
                raise ValueError(
                    f"The PopulationSim settings file already sets {name}: "
                    f"{original[name]!r}, but the tracked config requires {key}: {value!r}. "
                    "Remove the line from the settings file (braunschweig.population."
                    "popsim.settings_path) or change the config key; the tracked config "
                    "decides this setting.")
        if key not in original:
            pending.append(f"{key}: {_format_setting_value(value)}")
    result = settings_yaml
    if pending:
        result = _append_settings_lines(
            settings_yaml,
            ["# Set by the tracked config (braunschweig.population.popsim.*), not by this "
             "file:"] + pending)
        expected = dict(original)
        expected.update({key: value for key, value in overrides.items() if key not in original})
        if _parse_settings_mapping(result) != expected:
            raise ValueError(
                "Appending the tracked PopulationSim settings changed more than the "
                "appended keys; refusing to write altered settings.")
    logger.info("[popsim] batch settings carry the tracked PopulationSim options: %s",
                ", ".join(f"{key}={value!r}" for key, value in overrides.items()))
    return result


def integerizer_draw_in_settings(settings_yaml: str) -> str:
    """Return the ``INTEGERIZER_DRAW`` value of a settings text (``"off"`` when absent).

    A bare YAML ``off`` parses as boolean False; it is returned as ``"off"``.
    """
    value = _parse_settings_mapping(settings_yaml).get(INTEGERIZER_DRAW_SETTING)
    return "off" if value is None or value is False else str(value)


def batch_integerizer_seed(random_seed: int, batch_name: str) -> int:
    """Derive the integerizer seed of one batch from the pipeline seed and the folder name.

    Why one seed per batch: ADR-0199, decision 3. The folder name (e.g. ``batch_000``) is
    stable across runs and machines.

    Returns
    -------
    int
        A value in ``[0, 2**32)``: the first four bytes of the SHA-256 of
        ``populationsim-integerizer|<random_seed>|<batch_name>``, little endian.
    """
    digest = hashlib.sha256(
        f"populationsim-integerizer|{random_seed}|{batch_name}".encode()).digest()
    return int.from_bytes(digest[:4], "little")


def with_integerizer_seed(settings_yaml: str, seed: int) -> str:
    """Append ``INTEGERIZER_RANDOM_SEED: <seed>`` to a batch settings text.

    Raises
    ------
    ValueError
        If ``seed`` is not an integer in ``[0, 2**32)`` or the text already sets the key.
    """
    if (not isinstance(seed, int) or isinstance(seed, bool)
            or not 0 <= seed < _MAX_INTEGERIZER_SEED):
        raise ValueError(
            f"{INTEGERIZER_SEED_SETTING} must be an integer in [0, 2**32), got {seed!r}.")
    if INTEGERIZER_SEED_SETTING in _parse_settings_mapping(settings_yaml):
        raise ValueError(
            f"The batch settings already set {INTEGERIZER_SEED_SETTING}; the seed of each "
            "batch is derived by the pipeline (braunschweig.popsim.folders."
            "batch_integerizer_seed), remove the line from the settings file.")
    return _append_settings_lines(settings_yaml, [f"{INTEGERIZER_SEED_SETTING}: {seed}"])


def resolve_batch_settings_yaml(
    settings_yaml: str, write_data_dictionary: bool,
    overrides: Mapping[str, object] | None = None,
) -> str:
    """Return the settings text that is written into every PopulationSim batch folder.

    ``write_data_dictionary=True`` keeps the source text. ``False`` drops the
    diagnostic ``write_data_dictionary`` step via :func:`drop_data_dictionary_step`. Why
    that leaves the synthetic population unchanged, and how far that is checked: the
    comment of ``braunschweig.popsim.stage.config_keys.KEY_WRITE_DATA_DICTIONARY``. The
    decision is logged either way. ``overrides`` (not None) are then appended by
    :func:`apply_populationsim_overrides`; the per-batch integerizer seed is added later,
    per folder, by :func:`with_integerizer_seed`.
    """
    if write_data_dictionary:
        logger.info("[popsim] batch settings keep PopulationSim step '%s' "
                    "(braunschweig.population.popsim.write_data_dictionary=true)",
                    DATA_DICTIONARY_STEP)
        result = settings_yaml
    else:
        result = drop_data_dictionary_step(settings_yaml)
        logger.info("[popsim] batch settings drop PopulationSim step '%s' "
                    "(braunschweig.population.popsim.write_data_dictionary=false); "
                    "data_dict.csv/.txt are not written", DATA_DICTIONARY_STEP)
    if overrides is not None:
        result = apply_populationsim_overrides(result, overrides)
    return result


def _resolve_parent_kreis(
    xwalk: pd.DataFrame, weights: np.ndarray
) -> pd.DataFrame:
    """Collapse each 1 km parent's ``KREIS`` to its single dominant Kreis.

    A 1 km grid cell may straddle a Kreis border (its 100 m children carry
    different ARS). PopulationSim's geography hierarchy requires strict nesting
    (each 1 km maps to exactly one parent Kreis), so each parent is assigned the
    Kreis with the largest summed ``weights`` across its cells; ties break on the
    higher Kreis id (deterministic). Reassigned (boundary) cells are logged.

    Universe alignment (issue #147, sub-item 1, corrected): the batch KREIS backbone
    built here uses this RESOLVED dominant Kreis per 1 km parent, and the per-Kreis
    attribute-control targets in ``stage.py`` now partition households/persons by the
    SAME resolved Kreis (via :func:`braunschweig.popsim.mid.resolved_kreis_per_cell`),
    rather than the raw ``ARS[:5]`` of each 100 m cell. Previously the two universes
    disagreed for the ~0.1 % of border cells reassigned above (100 % run: ~48/43598):
    the category targets summed the raw-ARS Kreis total while the 100 m backbone summed
    the resolved cells. Region-wide per-Kreis sums are unchanged either way (a 1 km
    parent is atomic to one resolved Kreis); the alignment only moves those border
    cells' target attribution onto the Kreis that actually constrains them.
    """
    work = xwalk[[GEO_1KM, GEO_KREIS]].copy()
    work["_w"] = weights
    by_pair = work.groupby([GEO_1KM, GEO_KREIS], sort=False)["_w"].sum().reset_index()
    dominant = (
        by_pair.sort_values(["_w", GEO_KREIS])
        .groupby(GEO_1KM, sort=False)
        .tail(1)
        .set_index(GEO_1KM)[GEO_KREIS]
    )
    new_kreis = xwalk[GEO_1KM].map(dominant).to_numpy()
    n_reassigned = int((new_kreis != xwalk[GEO_KREIS].to_numpy()).sum())
    if n_reassigned:
        logger.info(
            "[popsim.folders] geo_crosswalk: resolved %d/%d 100m cells to their 1km "
            "parent's dominant Kreis (border cells straddling Kreis boundaries).",
            n_reassigned, len(xwalk),
        )
    out = xwalk.copy()
    out[GEO_KREIS] = new_kreis
    return out


def build_geo_crosswalk(
    df_100m: pd.DataFrame,
    *,
    id_col_100m: str = "GITTER_ID_100m",
    parent_col: str = "GITTER_ID_1km",
    ars_col: str | None = None,
    resolve_parent_kreis: bool = False,
    kreis_weight_col: str | None = None,
) -> pd.DataFrame:
    """Build the PopulationSim geo crosswalk for the given 100 m cells.

    One row per 100 m cell with its nested geography keys:
    ``[ZENSUS100m, ZENSUS1km, STAAT, WELT]``. STAAT and WELT are constant 1 (a
    single national / world seed geography). The 1 km parent is taken from the
    explicit parent column when present, else derived from the 100 m id.

    When ``ars_col`` is given (and present), an additional ``KREIS`` column is
    appended: each 100 m cell maps unambiguously to its Kreis = the first 5 digits
    of the cell's Amtlicher Regionalschluessel (ARS). This is the Tier-3
    (employment/education) control geography. Omitted when ``ars_col`` is None, so
    the default 4-column output is unchanged.

    Parameters
    ----------
    df_100m:
        Frame of 100 m cells (must carry ``id_col_100m``; ``parent_col`` optional;
        ``ars_col`` optional, needed only for the KREIS level).
    id_col_100m / parent_col:
        Source column names in ``df_100m``.
    ars_col:
        Optional source column holding the cell's 12-digit ARS; when present a
        ``KREIS`` column (ARS[:5]) is added.

    Returns
    -------
    pandas.DataFrame
        Columns ``[ZENSUS100m, ZENSUS1km, STAAT, WELT]`` (plus ``KREIS`` when
        ``ars_col`` is given).
    """
    if parent_col in df_100m.columns:
        parent = df_100m[parent_col].astype(str)
    else:
        parent = df_100m[id_col_100m].map(cells.derive_1km_parent_id)

    xwalk = pd.DataFrame(
        {
            GEO_100M: df_100m[id_col_100m].astype(str).to_numpy(),
            GEO_1KM: parent.to_numpy(),
            GEO_STAAT: 1,
            GEO_WELT: 1,
        }
    )
    # Optional KREIS level for Tier-3 controls: each 100 m cell maps to its Kreis =
    # the first 5 digits of the cell's ARS. With ``resolve_parent_kreis`` the raw
    # per-cell Kreis is then collapsed to one dominant Kreis per 1 km parent so the
    # WELT > STAAT > KREIS > ZENSUS1km > ZENSUS100m hierarchy nests strictly.
    if ars_col is not None and ars_col in df_100m.columns:
        # zfill(12) before slicing the Kreis prefix so an ARS that lost a leading
        # zero (e.g. read as an int) cannot silently join to the wrong Kreis --
        # the same guard `stage.derive_geo_kreis_from_ars` applies, kept consistent
        # here so this crosswalk and `mid.resolved_kreis_per_cell` never drift.
        xwalk[GEO_KREIS] = df_100m[ars_col].astype(str).str.zfill(12).str[:5].to_numpy()
        if resolve_parent_kreis:
            if kreis_weight_col is not None and kreis_weight_col in df_100m.columns:
                weights = pd.to_numeric(
                    df_100m[kreis_weight_col], errors="coerce"
                ).fillna(0.0).to_numpy()
            else:
                if kreis_weight_col is not None:
                    # Fallback transparency (CLAUDE.md): the caller asked for a
                    # population-weighted dominant-Kreis resolution but the
                    # column is absent (e.g. silently skipped at parquet load)
                    # -- uniform weights change which Kreis border cells
                    # resolve to, so this must never happen silently.
                    logger.warning(
                        "[popsim.folders] build_geo_crosswalk: requested "
                        "kreis_weight_col %r is absent from the cells frame -> "
                        "falling back to UNIFORM weights for the dominant-Kreis "
                        "resolution of 1 km parents (border-cell Kreis "
                        "assignment may differ from the population-weighted "
                        "intent).", kreis_weight_col,
                    )
                weights = np.ones(len(df_100m))
            xwalk = _resolve_parent_kreis(xwalk, weights)
    return xwalk


def build_control_totals(
    per_cell_targets: pd.DataFrame,
    geo_crosswalk: pd.DataFrame,
    *,
    target_cols: Sequence[str],
    cell_id_col: str = GEO_100M,
    parent_col: str = GEO_1KM,
) -> dict[str, pd.DataFrame]:
    """Build hierarchically-consistent control totals for all geographies.

    For each target column the 100 m values are integerized within their 1 km
    parent (largest-remainder), so the integer 100 m values sum exactly to the
    rounded 1 km target. The coarser geographies are then exact aggregates:

    - ``ZENSUS100m``: cell id + integerized target columns.
    - ``ZENSUS1km``: parent id + sum of the integerized 100 m children.
    - ``STAAT`` / ``WELT``: a single row (code 1) with the national totals.

    Parameters
    ----------
    per_cell_targets:
        Frame with ``cell_id_col`` and one float column per control target.
    geo_crosswalk:
        The crosswalk from :func:`build_geo_crosswalk` (gives the 1 km parent of
        each 100 m cell).
    target_cols:
        The control target columns to integerize and aggregate.

    Returns
    -------
    dict[str, pandas.DataFrame]
        Keyed by geography name (``ZENSUS100m`` / ``ZENSUS1km`` / ``STAAT`` /
        ``WELT``).
    """
    parent_of = geo_crosswalk.set_index(GEO_100M)[parent_col]
    merged = per_cell_targets.copy()
    merged[parent_col] = merged[cell_id_col].map(parent_of)
    if merged[parent_col].isna().any():
        missing = merged.loc[merged[parent_col].isna(), cell_id_col].tolist()
        raise ValueError(
            f"per_cell_targets has cells absent from the crosswalk: {missing[:5]}"
        )

    df_100m = pd.DataFrame({cell_id_col: merged[cell_id_col].to_numpy()})
    for col in target_cols:
        df_100m[col] = controls.integerize_within_parents(
            merged, value_col=col, parent_col=parent_col
        ).to_numpy()

    # 1 km = exact sum of the integerized 100 m children.
    df_100m_with_parent = df_100m.copy()
    df_100m_with_parent[parent_col] = merged[parent_col].to_numpy()
    df_1km = (
        df_100m_with_parent.groupby(parent_col, sort=False)[list(target_cols)]
        .sum()
        .reset_index()
        .rename(columns={parent_col: GEO_1KM})
    )

    national = {col: int(df_100m[col].sum()) for col in target_cols}
    df_staat = pd.DataFrame([{GEO_STAAT: 1, **national}])
    df_welt = pd.DataFrame([{GEO_WELT: 1, **national}])

    return {GEO_100M: df_100m, GEO_1KM: df_1km, GEO_STAAT: df_staat, GEO_WELT: df_welt}


def build_kreis_control_totals(
    kreis_table: pd.DataFrame,
    geo_crosswalk: pd.DataFrame,
    *,
    controls_map: Mapping[str, Sequence[str]],
    ars_col: str = "ARS_kreis",
    apportion_weights: Mapping[str, float] | None = None,
    household_apportion_weights: Mapping[str, float] | None = None,
    household_control_names: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Build the KREIS control-totals table from an imported per-Kreis census table.

    Unlike :func:`build_control_totals` (which integerizes + aggregates CELL columns
    up the hierarchy), Tier-3 marginals (employment / education) do not live in the
    grid cells; they come from a separate per-Kreis census table (the cleancensus
    ``kreis_*`` tables). For each Kreis present in ``geo_crosswalk[KREIS]`` (deduped,
    sorted), look up its row in ``kreis_table`` (keyed by ``ars_col``) and set each
    control target to the row-sum of its census-source columns (a coarse class = the
    sum of its category columns). Suppressed (NaN) components are treated as 0 by the
    row-sum.

    Per-batch apportionment
    -----------------------
    RegioStaR stratification can split ONE Kreis across MULTIPLE batches (a batch is a
    cell-disjoint sub-region). Each such batch must target only ITS share of the Kreis
    marginal, not the full Kreis marginal -- otherwise every batch holding a fraction
    of the Kreis targets the whole count, and (summed over batches) PopulationSim is
    constrained to N times the true marginal, saturating the control. ``apportion_weights``
    is ``{kreis: share}`` (the batch's population fraction of that Kreis); each control's
    per-Kreis value is multiplied by that share. A Kreis absent from the map (or a
    ``None`` map) keeps weight ``1.0`` = the full marginal. When the map gives every
    batch its population share of the Kreis, the per-Kreis targets sum (across batches)
    to exactly the full marginal.

    Parameters
    ----------
    kreis_table:
        Per-Kreis census marginals (one row per Kreis; ``ars_col`` + the source
        category columns).
    geo_crosswalk:
        The crosswalk from :func:`build_geo_crosswalk` built with ``ars_col`` so it
        carries a ``KREIS`` column.
    controls_map:
        ``{control_name: census_source_cols}`` (e.g. the catalog's tier3 entries).
    ars_col:
        The Kreis-key column in ``kreis_table`` (default ``"ARS_kreis"``).
    apportion_weights:
        Optional ``{kreis: float share}`` to scale each Kreis's controls (the batch's
        population share of the Kreis). ``None`` (default) -> full marginal, legacy
        behaviour byte-identical (so single-batch / pre-Tier-3 callers are unchanged).
        A Kreis missing from the map defaults to ``1.0`` (full marginal). Used for
        PERSON-level controls (employment / education / trip_class), where the batch's
        share of the Kreis population is the construct-correct apportionment basis.
    household_apportion_weights:
        Optional ``{kreis: float share}`` = the batch's HOUSEHOLD share of the Kreis,
        applied instead of ``apportion_weights`` to the controls named in
        ``household_control_names`` (issue #148). Where persons-per-household varies
        across a Kreis's batches, a household-level target scaled by the population
        share is inconsistent with that batch's household backbone; the household share
        is the correct basis. ``None`` (default) -> household controls fall back to
        ``apportion_weights`` (legacy population-share behaviour, byte-identical).
    household_control_names:
        Optional set/iterable of control names (keys of ``controls_map``) that are household-level
        and must use ``household_apportion_weights``. Controls not listed here keep
        ``apportion_weights``. ``None`` (default) -> every control uses
        ``apportion_weights`` (legacy).

    Returns
    -------
    pandas.DataFrame
        Columns ``[KREIS, <control_name>...]``, one row per distinct crosswalk Kreis.

    Raises
    ------
    ValueError
        If a crosswalk Kreis is absent from ``kreis_table`` or a source column is
        missing (fail-fast: no silently under-constrained control).
    """
    kreise = sorted(pd.Series(geo_crosswalk[GEO_KREIS].astype(str).unique()))
    table = kreis_table.copy()
    table[ars_col] = table[ars_col].astype(str)
    table = table.set_index(ars_col)

    missing_kreise = [k for k in kreise if k not in table.index]
    if missing_kreise:
        raise ValueError(
            f"kreis_table is missing Kreis rows present in the crosswalk: {missing_kreise[:5]}"
        )
    all_sources = {col for cols in controls_map.values() for col in cols}
    missing_cols = sorted(c for c in all_sources if c not in table.columns)
    if missing_cols:
        raise ValueError(f"kreis_table is missing source column(s): {missing_cols}")

    household_names = set(household_control_names) if household_control_names else set()
    if household_names and household_apportion_weights is None and apportion_weights is not None:
        # Observability (CLAUDE.md no-silent-fallback): household controls were named but
        # no household share was supplied, so they fall back to the population share. Not
        # reachable via run_popsim_mid (which always supplies the household total when
        # household controls are active); only a direct caller can hit this.
        logger.debug(
            "[popsim.folders] household controls %s apportioned by POPULATION share "
            "(no household_apportion_weights supplied).", sorted(household_names),
        )
    out: dict[str, object] = {GEO_KREIS: kreise}
    for name, source_cols in controls_map.items():
        # skipna suppression (NaN -> 0) in the multi-source row-sum is made
        # observable per issue #150 (fallback-transparency rule, CLAUDE.md).
        values = cells.sum_columns_logging_nan(
            table.loc[kreise], list(source_cols), f"KREIS control {name!r}"
        ).to_numpy()
        # Household-level controls are apportioned by the batch's HOUSEHOLD share when
        # available; person-level controls (and the legacy path) use the population
        # share (issue #148). A Kreis missing from the chosen map keeps weight 1.0.
        use_household = name in household_names and household_apportion_weights is not None
        weight_map = household_apportion_weights if use_household else apportion_weights
        if weight_map is not None:
            weights = np.array(
                [float(weight_map.get(k, 1.0)) for k in kreise], dtype=float
            )
            values = values * weights
        out[name] = values
    return pd.DataFrame(out)


def write_popsim_folder(
    folder: Union[str, Path],
    *,
    geo_crosswalk: pd.DataFrame,
    control_totals: Mapping[str, pd.DataFrame],
    controls_csv: pd.DataFrame,
    seed_households: pd.DataFrame,
    seed_persons: pd.DataFrame,
    settings_yaml: str | None = None,
    logging_yaml: str | None = None,
) -> dict[str, Path]:
    """Write a complete PopulationSim run folder; return the paths written.

    Creates ``data/``, ``configs/`` and an empty ``output/`` directory and writes
    the crosswalk, the four control-total tables, the seed tables, the controls
    spec and (when provided) the settings / logging YAML.

    Parameters
    ----------
    folder:
        Target run-folder path (created, with parents).
    control_totals:
        Must contain all four required geographies (see :data:`CONTROL_GEOGRAPHIES`).
        May additionally contain the optional Tier-3 ``KREIS`` table, which is then
        written as ``control_totals_KREIS.csv`` (absent for tier0-2, byte-identical).

    Raises
    ------
    ValueError
        If ``control_totals`` is missing any required geography (fail-fast: a
        partial control set would silently under-constrain the run).
    """
    missing = [g for g in CONTROL_GEOGRAPHIES if g not in control_totals]
    if missing:
        raise ValueError(
            f"control_totals is missing geographies {missing}; expected all of "
            f"{list(CONTROL_GEOGRAPHIES)}."
        )

    base = Path(folder)
    data_dir = base / "data"
    configs_dir = base / "configs"
    output_dir = base / "output"
    for directory in (data_dir, configs_dir, output_dir):
        directory.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}

    def _csv(df: pd.DataFrame, path: Path) -> None:
        df.to_csv(path, index=False)
        written[path.name] = path

    _csv(geo_crosswalk, data_dir / "geo_cross_walk.csv")
    for geography in CONTROL_GEOGRAPHIES:
        _csv(control_totals[geography], data_dir / f"control_totals_{geography}.csv")
    # Optional Tier-3 KREIS geography: written only when present so tier0-2 runs
    # (no KREIS in control_totals) stay byte-identical to the pre-Tier-3 baseline.
    if GEO_KREIS in control_totals:
        _csv(control_totals[GEO_KREIS], data_dir / f"control_totals_{GEO_KREIS}.csv")
    _csv(seed_households, data_dir / "seed_households.csv")
    _csv(seed_persons, data_dir / "seed_persons.csv")
    _csv(controls_csv, configs_dir / "controls.csv")

    if settings_yaml is not None:
        path = configs_dir / "settings.yaml"
        path.write_text(settings_yaml, encoding="utf-8")
        written[path.name] = path
    if logging_yaml is not None:
        path = configs_dir / "logging.yaml"
        path.write_text(logging_yaml, encoding="utf-8")
        written[path.name] = path

    return written
