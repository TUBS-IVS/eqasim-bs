"""Extract the committed MiD 2023 W_ZWD subtype-group reference (issue #242, sub-project C).

Reads the LOCAL raw MiD 2023 Wege table and writes one small aggregate table to ``--out-dir``
(default ``eqasim-data/data/braunschweig/mid``):

    mid2023_w_zwd_group_reference.csv   (W_GEW-weighted share of each W_ZWD subtype group among
                                         the LABELLED legs of its purpose, plus weighted
                                         wegkm_imp percentiles, for BOTH settings of
                                         purpose_subtype_codeplan_sentinels)

The groups are never re-typed here: they come from the specs the model itself estimates on --
``braunschweig.popsim.purpose_subtype.leisure_spec`` / ``other_errand_spec`` (whose ``_CODEPLAN``
variants are exactly what the ``purpose_subtype_codeplan_sentinels`` flag selects) and
``braunschweig.popsim.shop_subtype``'s daily/non-daily W_ZWD sets. The ``spec_variant`` column
says which setting a row was measured under, so the flag's effect on the estimated mix is visible
in the committed file instead of having to be re-derived.

Role: a MEASUREMENT REFERENCE for ``scripts/compare_purpose_subtypes_srv.py``, which puts it
beside the regional SrV fine-purpose reference. NOT a control target and NOT a validated
calibration target: no stage reads it, and nothing is re-estimated from it here.

Filter and weights follow ``scripts/extract_mid_w_zweck_hwzweck1.py`` exactly -- its weekday and
rbW constants are IMPORTED rather than repeated, so the two committed MiD Wege aggregates cannot
silently drift onto different leg universes.

Usage (eqasim env, from a worktree; point --raw at the local raw directory):
    python scripts/extract_mid_w_zwd_groups.py \
        --raw C:/Users/bienzeisler/Documents/GitHub/popsimprep/inputs/MiD2023/MiD2023_B1_Datensatzpaket/CSV \
        --out-dir eqasim-data/data/braunschweig/mid --source-commit <sha>
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.calibration.srv_distance_targets import weighted_quantiles  # noqa: E402
from braunschweig.calibration.srv_fine_purpose import QUANTILE_PROBABILITIES  # noqa: E402
from braunschweig.popsim.mid.csv_format import detect_csv_separator  # noqa: E402
from braunschweig.popsim.purpose_subtype import (  # noqa: E402
    SubtypeSpec, code_coverage_guard, leisure_spec, other_errand_spec,
)
from braunschweig.popsim.shop_subtype import (  # noqa: E402
    SHOP_DAILY_W_ZWD, SHOP_DETAIL_MISSING, SHOP_NONDAILY_W_ZWD,
)
from scripts.extract_mid_w_zweck_hwzweck1 import (  # noqa: E402
    KERNWO_WEEKDAY_CODES, RBW_SUMMARY_LEG_CODE,
)

RAW_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "popsim" / "mid2023_raw"
OUT_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "mid"
WEGE_FILE = "MiD2023_Wege.csv"
MID_GROUP_TABLE = "mid2023_w_zwd_group_reference.csv"

REQUIRED_COLUMNS = ["W_ZWECK", "W_ZWD", "W_GEW", "W_RBW", "kernwo", "wegkm_imp"]
GROUP_COLUMNS = ["purpose", "spec_variant", "group", "n_unweighted", "share_within_purpose",
                 "km_p25", "km_p50", "km_p75", "n_missing_distance"]

#: ``wegkm_imp`` values at or above this bound are MiD missing-value codes (9994 upwards), not
#: distances; they are excluded from the percentiles and counted separately.
DISTANCE_MISSING_CODE_MIN = 9994.0

#: The two settings of the ``purpose_subtype_codeplan_sentinels`` config key
#: (``braunschweig/popsim/stage/config_keys.py``): "default" = flag OFF (LEISURE_SPEC /
#: OTHER_ERRAND_SPEC), "codeplan" = flag ON (the _CODEPLAN variants, in which the no-detail codes
#: W_ZWD 799 "Freizeit k.A." and 699 "Erledigung k.A." are sentinels instead of group members).
SPEC_VARIANTS = ("default", "codeplan")

#: The shopping split of ``braunschweig.popsim.shop_subtype`` expressed as a SubtypeSpec so all
#: three purposes go through ONE code path (coverage guard, labelled filter, weighted share). The
#: W_ZWD code sets are imported, never retyped; only the W_ZWECK filter value 4 is written out
#: here, because shop_subtype hard-codes it inline (``mid_wege["W_ZWECK"] == 4``) instead of
#: exposing a constant, and this script must not edit that module (issue #242 Task 6 scope).
SHOP_SPEC = SubtypeSpec(
    purpose_label="shop",
    zweck_values=frozenset({4}),
    groups={"shop_daily": frozenset(SHOP_DAILY_W_ZWD),
            "shop_non_daily": frozenset(SHOP_NONDAILY_W_ZWD)},
    sentinels=frozenset(SHOP_DETAIL_MISSING),
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("extract_mid_w_zwd_groups")
_LOG_TAG = "[mid w_zwd groups]"


def specs_for_variant(variant: str) -> tuple:
    """The three subtype specs measured under one ``purpose_subtype_codeplan_sentinels`` setting.

    The shopping spec is identical in both variants (the flag only moves the two no-detail codes
    799 / 699 of the leisure and errand specs), so its rows repeat by construction -- reported
    rather than suppressed, so a consumer can read every group under both variants uniformly.
    """
    if variant not in SPEC_VARIANTS:
        raise ValueError("%s unknown spec variant %r (known: %s)" % (_LOG_TAG, variant,
                                                                     list(SPEC_VARIANTS)))
    codeplan_sentinels = variant == "codeplan"
    return (SHOP_SPEC, other_errand_spec(codeplan_sentinels), leisure_spec(codeplan_sentinels))


def filter_weekday_legs(wege: pd.DataFrame) -> pd.DataFrame:
    """Weekday, non-rbW legs -- the same universe as scripts/extract_mid_w_zweck_hwzweck1.py."""
    frame = wege.copy()
    for column in REQUIRED_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    filtered = frame[frame["kernwo"].isin(KERNWO_WEEKDAY_CODES)
                     & (frame["W_RBW"] != RBW_SUMMARY_LEG_CODE)]
    n_total = len(frame)
    logger.info("%s kept %d/%d legs (%.2f%%) after the weekday (kernwo in %s) and non-rbW "
                "(W_RBW != %d) filter", _LOG_TAG, len(filtered), n_total,
                100.0 * len(filtered) / n_total if n_total else float("nan"),
                list(KERNWO_WEEKDAY_CODES), RBW_SUMMARY_LEG_CODE)
    if len(filtered) == 0:
        raise ValueError("%s no legs left after the weekday/non-rbW filter; check the kernwo and "
                         "W_RBW column contents." % _LOG_TAG)
    return filtered


def build_group_reference(wege: pd.DataFrame) -> tuple:
    """W_GEW-weighted subtype-group shares and wegkm_imp percentiles, per spec variant.

    Parameters
    ----------
    wege : DataFrame
        Raw-shaped MiD Wege frame carrying :data:`REQUIRED_COLUMNS`.

    Returns
    -------
    tuple[DataFrame, dict]
        The table with :data:`GROUP_COLUMNS` and a diagnostics dict keyed
        ``"<variant>/<purpose>"`` carrying the leg counts behind every row (total legs of the
        purpose, labelled legs, sentinel legs, legs with a missing-distance code).

    Notes
    -----
    ``share_within_purpose`` is the W_GEW share among the LABELLED legs of the purpose -- exactly
    the universe ``purpose_subtype.estimate_group_probabilities`` estimates the model's
    probabilities on, so the share here is the mix the model actually reproduces. Sentinel legs
    (design codes, cross-purpose intrusions, and under the "codeplan" variant the two no-detail
    codes) are excluded from both numerator and denominator; the labelled rate is logged and
    written into the committed file's header.
    """
    missing = [column for column in REQUIRED_COLUMNS if column not in wege.columns]
    if missing:
        raise ValueError("%s MiD Wege table is missing required column(s) %s" % (_LOG_TAG, missing))
    filtered = filter_weekday_legs(wege)

    rows, diagnostics = [], {}
    for variant in SPEC_VARIANTS:
        for spec in specs_for_variant(variant):
            # Raises if a W_ZWD code observed on this purpose is neither grouped nor a sentinel:
            # an unmapped code would otherwise silently shrink the labelled denominator.
            code_coverage_guard(filtered, spec)
            purpose_legs = filtered[filtered["W_ZWECK"].isin(spec.zweck_values)]
            labelled = purpose_legs[purpose_legs["W_ZWD"].isin(spec.group_codes)].copy()
            n_purpose, n_labelled = len(purpose_legs), len(labelled)
            n_sentinel = int(purpose_legs["W_ZWD"].isin(spec.sentinels).sum())
            logger.info("%s %s/%s: labelled %d/%d legs (%.2f%%), sentinel %d (%.2f%%)", _LOG_TAG,
                        spec.purpose_label, variant, n_labelled, n_purpose,
                        100.0 * n_labelled / n_purpose if n_purpose else float("nan"),
                        n_sentinel, 100.0 * n_sentinel / n_purpose if n_purpose else float("nan"))
            if n_labelled == 0:
                raise ValueError(
                    "%s %s/%s: no labelled leg at all; the primary (W_ZWD group) path produced "
                    "nothing, which almost always means a code or column mismatch rather than a "
                    "real absence." % (_LOG_TAG, spec.purpose_label, variant))

            code_to_group = {code: name for name, codes in spec.groups.items() for code in codes}
            labelled["_group"] = labelled["W_ZWD"].map(code_to_group)
            weight_total = float(labelled["W_GEW"].astype(float).sum())
            distance_valid = (labelled["wegkm_imp"] < DISTANCE_MISSING_CODE_MIN) \
                & labelled["wegkm_imp"].notna()
            n_missing_distance_purpose = int((~distance_valid).sum())
            logger.info("%s %s/%s: wegkm_imp usable for %d/%d labelled legs (%.2f%%); %d carry a "
                        "missing-value code (>= %.0f) and are excluded from the percentiles",
                        _LOG_TAG, spec.purpose_label, variant,
                        n_labelled - n_missing_distance_purpose, n_labelled,
                        100.0 * (n_labelled - n_missing_distance_purpose) / n_labelled,
                        n_missing_distance_purpose, DISTANCE_MISSING_CODE_MIN)

            for group in sorted(spec.groups):
                member = labelled[labelled["_group"] == group]
                weights = member["W_GEW"].astype(float)
                share = float(weights.sum() / weight_total) if weight_total > 0 else float("nan")
                valid = member[(member["wegkm_imp"] < DISTANCE_MISSING_CODE_MIN)
                               & member["wegkm_imp"].notna()]
                p25, p50, p75 = weighted_quantiles(
                    np.asarray(valid["wegkm_imp"], dtype=float),
                    np.asarray(valid["W_GEW"], dtype=float), list(QUANTILE_PROBABILITIES))
                rows.append({"purpose": spec.purpose_label, "spec_variant": variant,
                             "group": group, "n_unweighted": int(len(member)),
                             "share_within_purpose": share, "km_p25": p25, "km_p50": p50,
                             "km_p75": p75,
                             "n_missing_distance": int(len(member) - len(valid))})
            diagnostics["%s/%s" % (variant, spec.purpose_label)] = {
                "n_purpose_legs": n_purpose, "n_labelled": n_labelled, "n_sentinel": n_sentinel,
                "n_missing_distance": n_missing_distance_purpose,
            }
    return pd.DataFrame(rows, columns=GROUP_COLUMNS), diagnostics


def check_invariants(table: pd.DataFrame) -> None:
    """Raise unless every (variant, purpose) block's shares sum to 1 and the percentiles order."""
    for (variant, purpose), block in table.groupby(["spec_variant", "purpose"]):
        total = float(block["share_within_purpose"].sum())
        if abs(total - 1.0) > 1e-9:
            raise ValueError("%s share_within_purpose of %s/%s sums to %r, not 1"
                             % (_LOG_TAG, variant, purpose, total))
    shares = table["share_within_purpose"].dropna()
    if ((shares < 0) | (shares > 1)).any():
        raise ValueError("%s share_within_purpose outside [0, 1]" % _LOG_TAG)
    ordered = table.dropna(subset=["km_p25", "km_p50", "km_p75"])
    if ((ordered["km_p25"] > ordered["km_p50"]) | (ordered["km_p50"] > ordered["km_p75"])).any():
        raise ValueError("%s wegkm_imp percentiles are not monotone (p25 <= p50 <= p75)" % _LOG_TAG)


def _header(table: pd.DataFrame, diagnostics: dict, source_commit: str) -> list:
    """Provenance header of the MiD group reference (source, role, universe, coverage)."""
    lines = [
        "# Source: MiD 2023 Wege (LOCAL raw %s; national scientific-use delivery, never" % WEGE_FILE,
        "#   committed), generated by scripts/extract_mid_w_zwd_groups.py on %s."
        % dt.date.today().isoformat(),
        "# Code state: eqasim-bs %s; groups from braunschweig.popsim.purpose_subtype"
        % source_commit,
        "#   (leisure_spec / other_errand_spec) and braunschweig.popsim.shop_subtype -- imported,",
        "#   never retyped.",
        "# Table: %s" % MID_GROUP_TABLE,
        "# Role: MEASUREMENT REFERENCE for scripts/compare_purpose_subtypes_srv.py (issue #242,",
        "#   sub-project C). NOT a control target and NOT a validated calibration target: no stage",
        "#   reads it, and nothing is re-estimated from it.",
        "# Universe: weekday legs (kernwo in %s) that are not route-break summary legs"
        % list(KERNWO_WEEKDAY_CODES),
        "#   (W_RBW != %d) -- the same filter as scripts/extract_mid_w_zweck_hwzweck1.py, whose"
        % RBW_SUMMARY_LEG_CODE,
        "#   constants this script imports. Within a purpose only LABELLED legs (a W_ZWD code that",
        "#   is a member of one of the spec's groups) enter the share, exactly as",
        "#   purpose_subtype.estimate_group_probabilities does; sentinel legs are excluded from",
        "#   numerator AND denominator.",
        "# Weights: W_GEW (MiD trip expansion weight).",
        "# spec_variant: the two settings of the purpose_subtype_codeplan_sentinels config key --",
        "#   'default' = flag OFF (LEISURE_SPEC / OTHER_ERRAND_SPEC), 'codeplan' = flag ON (the",
        "#   _CODEPLAN variants, in which the no-detail codes W_ZWD 799 'Freizeit k.A.' and 699",
        "#   'Erledigung k.A.' are sentinels instead of group members). The shop split has no",
        "#   codeplan variant, so its two blocks are identical by construction.",
        "# Coverage (labelled / sentinel / missing-distance legs per variant and purpose):",
    ]
    for key in sorted(diagnostics):
        stats = diagnostics[key]
        n_purpose = stats["n_purpose_legs"]
        lines.append(
            "#   %s: %d/%d legs labelled (%.2f%%), %d sentinel (%.2f%%), %d labelled legs carry a "
            "wegkm_imp" % (key, stats["n_labelled"], n_purpose,
                           100.0 * stats["n_labelled"] / n_purpose if n_purpose else float("nan"),
                           stats["n_sentinel"],
                           100.0 * stats["n_sentinel"] / n_purpose if n_purpose else float("nan"),
                           stats["n_missing_distance"]))
        lines.append("#     missing-value code (>= %.0f) and are excluded from the percentiles."
                     % DISTANCE_MISSING_CODE_MIN)
    lines += [
        "# Columns: purpose, spec_variant, group (the subtype group name the model uses),",
        "#   n_unweighted (labelled legs of the group), share_within_purpose (W_GEW share among the",
        "#   purpose's labelled legs; the shares of one purpose and variant sum to 1),",
        "#   km_p25/p50/p75 (W_GEW-weighted wegkm_imp percentiles in km, Hazen midpoint-CDF",
        "#   convention via srv_distance_targets.weighted_quantiles -- the same definition the SrV",
        "#   side uses, so the two are comparable), n_missing_distance (legs of the group excluded",
        "#   from the percentiles because wegkm_imp carries a missing-value code).",
        "# Rows: %d = %d spec variants x the groups of the three purposes."
        % (len(table), len(SPEC_VARIANTS)),
        "# Invariants (checked before writing, the extraction raises on a violation): the shares of",
        "#   each (spec_variant, purpose) block sum to 1 and lie inside [0, 1]; p25 <= p50 <= p75.",
        "# Generated by scripts/extract_mid_w_zwd_groups.py; regenerate there, never edit.",
    ]
    return lines


def _write(df: pd.DataFrame, path: Path, header: list) -> None:
    """Write the provenance-headed CSV (LF data rows, readable but lossless float format)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        for line in header:
            handle.write(line + "\n")
        df.to_csv(handle, index=False, lineterminator="\n", float_format="%.10g")
    logger.info("wrote %s (%d rows)", path, len(df))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=RAW_DEFAULT,
                        help="Directory holding the LOCAL raw MiD 2023 Wege CSV.")
    parser.add_argument("--out-dir", type=Path, default=OUT_DEFAULT,
                        help="Directory to write the committed reference table into.")
    parser.add_argument("--source-commit", required=True,
                        help="Git commit hash of the code state, recorded in the header.")
    args = parser.parse_args(argv)

    wege_path = args.raw / WEGE_FILE
    if not wege_path.exists():
        raise FileNotFoundError(
            "[extract_mid_w_zwd_groups] raw MiD Wege file not found: %s. Pass --raw pointing at "
            "the directory holding %s (LOCAL raw delivery, never committed)."
            % (wege_path, WEGE_FILE))
    separator = detect_csv_separator(wege_path)
    header = pd.read_csv(wege_path, sep=separator, nrows=0).columns
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise RuntimeError("[extract_mid_w_zwd_groups] %s is missing required column(s) %s"
                           % (wege_path, missing))
    wege = pd.read_csv(wege_path, sep=separator, usecols=REQUIRED_COLUMNS, low_memory=False)
    logger.info("%s read %d legs from %s", _LOG_TAG, len(wege), wege_path)

    table, diagnostics = build_group_reference(wege)
    check_invariants(table)
    logger.info("%s invariants passed; diagnostics: %s", _LOG_TAG, diagnostics)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    _write(table, args.out_dir / MID_GROUP_TABLE, _header(table, diagnostics, args.source_commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
