"""Extract the committed MiD 2023 W_ZWD subtype-group reference (issue #242, sub-project C).

Reads the LOCAL raw MiD 2023 Wege table and writes one small aggregate table to ``--out-dir``
(default ``eqasim-data/data/braunschweig/mid``):

    mid2023_w_zwd_group_reference.csv   (W_GEW-weighted share of each subtype group among the
                                         LABELLED legs of its purpose, plus weighted wegkm_imp
                                         percentiles, for every spec variant in
                                         :data:`SPEC_VARIANTS`)

The groups are never re-typed here: they come from the specs the model itself estimates on --
``braunschweig.popsim.purpose_subtype.leisure_spec`` / ``other_errand_spec`` (whose ``_CODEPLAN``
and ``_UNSPECIFIED`` variants are exactly what the ``purpose_subtype_codeplan_sentinels`` and
``leisure_unspecified_subtype`` flags select) and ``braunschweig.popsim.shop_subtype``'s
daily/non-daily W_ZWD sets. The ``spec_variant`` column says which flag combination a row was
measured under, so each flag's effect on the estimated mix is visible in the committed file
instead of having to be re-derived; :data:`SPEC_VARIANT_DESCRIPTIONS` states what each one is.

Role: a MEASUREMENT REFERENCE for ``scripts/compare_purpose_subtypes_srv.py``, which puts it
beside the regional SrV fine-purpose reference. NOT a control target and NOT a validated
calibration target: no stage reads it, and nothing is re-estimated from it here.

Filter and weights follow ``scripts/extract_mid_w_zweck_hwzweck1.py`` exactly -- its weekday and
rbW constants are IMPORTED rather than repeated, and the leg universe itself is applied through
``braunschweig.popsim.trips.weekday_diary_leg_mask`` (the ONE definition, which the model's own
MiD estimations use as well; ADR-0116), so the two committed MiD Wege aggregates cannot silently
drift onto different leg universes -- nor onto a different universe than the estimation.

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
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.calibration.srv_distance_targets import weighted_quantiles  # noqa: E402
from braunschweig.calibration.srv_fine_purpose import QUANTILE_PROBABILITIES  # noqa: E402
from braunschweig.popsim.diary_facts import WEGKM_CODE_MIN  # noqa: E402
from braunschweig.popsim.mid.csv_format import detect_csv_separator  # noqa: E402
from braunschweig.popsim.purpose_subtype import (  # noqa: E402
    SubtypeSpec, code_coverage_guard, label_legs, leisure_spec, other_errand_spec,
)
from braunschweig.popsim.shop_subtype import (  # noqa: E402
    SHOP_DAILY_W_ZWD, SHOP_DETAIL_MISSING, SHOP_NONDAILY_W_ZWD,
)
from braunschweig.popsim.trips import weekday_diary_leg_mask  # noqa: E402
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
#: distances; they are excluded from the percentiles and counted separately. Imported from
#: ``braunschweig.popsim.diary_facts`` so the repository keeps ONE definition of the bound --
#: note that diary_facts treats such a leg as 0 km, whereas this reference EXCLUDES it from the
#: percentiles (a reference percentile must not be pulled down by a substituted zero).
DISTANCE_MISSING_CODE_MIN = WEGKM_CODE_MIN

#: The measured settings of the two subtype config keys in
#: ``braunschweig/popsim/stage/config_keys.py``. This tuple is the ONE place the variant
#: vocabulary is defined: the committed file's header, the invariant checks and
#: ``scripts/compare_purpose_subtypes_srv.py``'s summary all read it (and
#: :data:`SPEC_VARIANT_DESCRIPTIONS`) rather than repeating the names.
SPEC_VARIANTS = ("default", "codeplan", "codeplan_unspecified")

#: What each spec variant measures, in one paragraph per variant. Rendered into the committed
#: file's provenance header AND into the comparison summary, so the two artefacts cannot describe
#: the same variant differently.
SPEC_VARIANT_DESCRIPTIONS = {
    "default":
        "purpose_subtype_codeplan_sentinels OFF and leisure_unspecified_subtype OFF "
        "(LEISURE_SPEC / OTHER_ERRAND_SPEC): the no-detail codes W_ZWD 799 'Freizeit k.A.' and "
        "699 'Erledigung k.A.' are ordinary members of leisure_activity / other_errand_long, and "
        "W_ZWECK 10 'anderer Zweck' legs are not measured at all.",
    "codeplan":
        "purpose_subtype_codeplan_sentinels ON, leisure_unspecified_subtype OFF (the _CODEPLAN "
        "variants, ADR-0113): 799 and 699 become sentinels and leave estimation entirely; "
        "W_ZWECK 10 legs are still not measured.",
    "codeplan_unspecified":
        "both keys ON -- the PRODUCTION composition since issue #373 (ADR-0115): the _CODEPLAN "
        "variants plus the fifth, W_ZWECK-defined leisure group leisure_unspecified, which holds "
        "the W_ZWECK 10 'anderer Zweck' legs that w_zweck_10_as_leisure realises as leisure "
        "(ADR-0111). The leisure denominator here is the labelled W_ZWECK 7 legs PLUS all "
        "W_ZWECK 10 legs, so the four W_ZWD groups' raw shares all shrink by ONE common factor "
        "while their shares WITHIN the comparable universe are unchanged from the codeplan "
        "variant. The shop and other-errand blocks are identical to the codeplan variant's.",
}

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

if set(SPEC_VARIANT_DESCRIPTIONS) != set(SPEC_VARIANTS):
    # The committed header and the comparison summary both render EVERY variant from this dict,
    # so a variant without a description would produce an unexplained block, and a description
    # without a variant would be dead text nothing renders. Checked at import, not at run time.
    raise ValueError(
        "%s SPEC_VARIANT_DESCRIPTIONS must describe exactly the variants of SPEC_VARIANTS; "
        "undescribed: %s; described but not a variant: %s"
        % (_LOG_TAG, sorted(set(SPEC_VARIANTS) - set(SPEC_VARIANT_DESCRIPTIONS)),
           sorted(set(SPEC_VARIANT_DESCRIPTIONS) - set(SPEC_VARIANTS))))


def specs_for_variant(variant: str) -> tuple:
    """The three subtype specs measured under one combination of the two subtype config keys.

    The shopping spec is identical in every variant (neither key touches it), so its rows repeat
    by construction -- reported rather than suppressed, so a consumer can read every group under
    every variant uniformly. The same holds for the errand spec between "codeplan" and
    "codeplan_unspecified", which differ only in the leisure spec's fifth, W_ZWECK-defined group.
    """
    if variant not in SPEC_VARIANTS:
        raise ValueError("%s unknown spec variant %r (known: %s)" % (_LOG_TAG, variant,
                                                                     list(SPEC_VARIANTS)))
    codeplan_sentinels = variant in ("codeplan", "codeplan_unspecified")
    unspecified_subtype = variant == "codeplan_unspecified"
    return (SHOP_SPEC, other_errand_spec(codeplan_sentinels),
            leisure_spec(codeplan_sentinels, unspecified_subtype))


def filter_weekday_legs(wege: pd.DataFrame) -> tuple:
    """The WEEKDAY DIARY universe -- weekday, non-rbW legs.

    The universe itself is NOT defined here: the mask comes from
    ``braunschweig.popsim.trips.weekday_diary_leg_mask``, the ONE definition the model's own
    MiD estimations apply as well (the secondary distance layers and the three subtype
    deciders, under ``secondary_mid_weekday_legs_only``; ADR-0116), and the sibling extraction
    ``scripts/extract_mid_w_zweck_hwzweck1.py`` calls the same helper -- so this committed
    reference and the estimation it is compared against cannot describe different days.

    Returns ``(filtered, diagnostics)``. The diagnostics carry the raw and kept leg counts and,
    per column, how many values became NaN under the ``errors="coerce"`` numeric coercion: a
    coercion that silently turns a mis-parsed column into NaN would shrink a filter's universe
    without any signal, so the rate is logged (and, for the weight, escalated to an error below).
    """
    frame = wege.copy()
    n_total = len(frame)
    coerced_to_nan = {}
    for column in REQUIRED_COLUMNS:
        before = frame[column].notna()
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        n_coerced = int((before & frame[column].isna()).sum())
        coerced_to_nan[column] = n_coerced
        if n_coerced:
            logger.warning("%s %d/%d values (%.2f%%) of column %s became NaN under the numeric "
                           "coercion", _LOG_TAG, n_coerced, n_total,
                           100.0 * n_coerced / n_total if n_total else float("nan"), column)
    logger.info("%s numeric coercion produced NaN for %s (of %d legs)", _LOG_TAG,
                ", ".join("%s=%d" % item for item in coerced_to_nan.items()), n_total)

    invalid_weight = ~(frame["W_GEW"] > 0)
    n_invalid_weight = int(invalid_weight.sum())
    if n_invalid_weight:
        raise ValueError(
            "%s %d/%d legs (%.2f%%) have a missing or non-positive W_GEW; a weighted share and a "
            "weighted percentile are undefined for them, and a delivery in which they occur must "
            "be investigated rather than silently filtered."
            % (_LOG_TAG, n_invalid_weight, n_total,
               100.0 * n_invalid_weight / n_total if n_total else float("nan")))

    filtered = frame[weekday_diary_leg_mask(frame)]
    logger.info("%s kept %d/%d legs (%.2f%%) after the weekday (kernwo in %s) and non-rbW "
                "(W_RBW != %d) filter", _LOG_TAG, len(filtered), n_total,
                100.0 * len(filtered) / n_total if n_total else float("nan"),
                list(KERNWO_WEEKDAY_CODES), RBW_SUMMARY_LEG_CODE)
    if len(filtered) == 0:
        raise ValueError("%s no legs left after the weekday/non-rbW filter; check the kernwo and "
                         "W_RBW column contents." % _LOG_TAG)
    diagnostics = {"n_legs_raw": n_total, "n_legs_after_weekday_rbw": int(len(filtered)),
                   "n_legs_invalid_weight": n_invalid_weight,
                   "n_values_coerced_to_nan": coerced_to_nan}
    return filtered, diagnostics


def build_group_reference(wege: pd.DataFrame) -> tuple:
    """W_GEW-weighted subtype-group shares and wegkm_imp percentiles, per spec variant.

    Parameters
    ----------
    wege : DataFrame
        Raw-shaped MiD Wege frame carrying :data:`REQUIRED_COLUMNS`.

    Returns
    -------
    tuple[DataFrame, dict]
        The table with :data:`GROUP_COLUMNS` and a diagnostics dict: the leg-universe counts of
        :func:`filter_weekday_legs` (``n_legs_raw``, ``n_legs_after_weekday_rbw``, ...) plus one
        entry per ``"<variant>/<purpose>"`` carrying the leg counts behind every row (total legs
        of the purpose, labelled legs, sentinel legs, legs with a missing-distance code).

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
    filtered, universe_diagnostics = filter_weekday_legs(wege)

    rows, diagnostics = [], dict(universe_diagnostics)
    for variant in SPEC_VARIANTS:
        for spec in specs_for_variant(variant):
            # Raises if a W_ZWD code observed on this purpose is neither grouped nor a sentinel:
            # an unmapped code would otherwise silently shrink the labelled denominator.
            code_coverage_guard(filtered, spec)
            purpose_legs = filtered[filtered["W_ZWECK"].isin(spec.zweck_values)]

            # ONE labelling rule for the whole model: purpose_subtype.label_legs is what
            # estimate_group_probabilities uses too (zweck group first, then W_ZWD detail group),
            # so this reference cannot describe a mix the estimation does not see. label_legs also
            # emits the WARNING when a W_ZWECK group overrides a valid detail code.
            labelled, n_by_zweck, n_zweck_override = label_legs(purpose_legs, spec)

            n_purpose, n_labelled = len(purpose_legs), len(labelled)
            # A leg whose W_ZWECK puts it in a zweck group is LABELLED, not excluded, even though
            # its W_ZWD is a design sentinel -- counting it as a sentinel would report a group's
            # own legs as an exclusion in the committed coverage header.
            n_sentinel = int((purpose_legs["W_ZWD"].isin(spec.sentinels)
                              & ~purpose_legs["W_ZWECK"].isin(spec.zweck_group_codes)).sum())
            logger.info("%s %s/%s: labelled %d/%d legs (%.2f%%), sentinel %d (%.2f%%), "
                        "%d by W_ZWECK group (%d of those overriding a valid W_ZWD group code)",
                        _LOG_TAG, spec.purpose_label, variant, n_labelled, n_purpose,
                        100.0 * n_labelled / n_purpose if n_purpose else float("nan"),
                        n_sentinel, 100.0 * n_sentinel / n_purpose if n_purpose else float("nan"),
                        n_by_zweck, n_zweck_override)
            if n_labelled == 0:
                raise ValueError(
                    "%s %s/%s: no labelled leg at all; the primary (W_ZWD group / W_ZWECK group) "
                    "path produced nothing, which almost always means a code or column mismatch "
                    "rather than a real absence." % (_LOG_TAG, spec.purpose_label, variant))

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

            for group in spec.group_names:
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
                "n_by_zweck_group": n_by_zweck, "n_zweck_group_override": n_zweck_override,
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


def _comment_paragraph(text: str, *, indent: str = "#   ", hanging: str = "#     ",
                       width: int = 96) -> list:
    """Wrap one paragraph of prose into ``#``-prefixed header lines (first indented, rest hanging).

    Used for the per-variant descriptions, which live in :data:`SPEC_VARIANT_DESCRIPTIONS` as
    plain sentences so the SAME text can be rendered into the comparison summary.
    """
    return [(indent if index == 0 else hanging) + line
            for index, line in enumerate(textwrap.wrap(text, width=width - len(hanging)))]


def _header(table: pd.DataFrame, diagnostics: dict, source_commit: str) -> list:
    """Provenance header of the MiD group reference (source, role, universe, coverage)."""
    lines = [
        "# Source: MiD 2023 Wege (LOCAL raw %s; national scientific-use delivery, never" % WEGE_FILE,
        "#   committed), generated by scripts/extract_mid_w_zwd_groups.py on %s."
        % dt.date.today().isoformat(),
        "# Code state at generation: eqasim-bs %s (--source-commit, 8-character hash of the"
        % source_commit,
        "#   eqasim-bs commit whose code produced this file); groups from",
        "#   braunschweig.popsim.purpose_subtype (leisure_spec / other_errand_spec) and",
        "#   braunschweig.popsim.shop_subtype -- imported, never retyped.",
        "# Table: %s" % MID_GROUP_TABLE,
        "# Role: MEASUREMENT REFERENCE for scripts/compare_purpose_subtypes_srv.py (issue #242,",
        "#   sub-project C). NOT a control target and NOT a validated calibration target: no stage",
        "#   reads it, and nothing is re-estimated from it.",
        "# Universe: weekday legs (kernwo in %s) that are not route-break summary legs"
        % list(KERNWO_WEEKDAY_CODES),
        "#   (W_RBW != %d) -- the WEEKDAY DIARY universe of" % RBW_SUMMARY_LEG_CODE,
        "#   braunschweig.popsim.trips.weekday_diary_leg_mask, applied through that ONE helper",
        "#   (scripts/extract_mid_w_zweck_hwzweck1.py, whose constants this script imports, calls",
        "#   it too). A purpose's universe is the legs whose W_ZWECK is in",
        "#   its spec's zweck_values, and only LABELLED legs of that universe enter the share,",
        "#   labelled by the very same purpose_subtype.label_legs helper the model's estimation",
        "#   uses. Both the labelling RULE and the leg UNIVERSE are shared with the estimation:",
        "#   the subtype deciders and the distance layers apply the SAME weekday_diary_leg_mask",
        "#   under secondary_mid_weekday_legs_only (ADR-0116, which closes the two-universe",
        "#   difference ADR-0115 recorded); with that flag off they label every delivered Wege",
        "#   row instead and their shares differ from the WEEKDAY shares measured here. A leg is",
        "#   LABELLED either by a W_ZWD code that is a member of one of the spec's groups, or by a",
        "#   W_ZWECK-defined group (spec.zweck_groups), which wins over the detail code.",
        "#   Sentinel legs are excluded from numerator AND",
        "#   denominator; a leg labelled by a W_ZWECK group is NOT a sentinel leg even though its",
        "#   W_ZWD is a design sentinel, so it is counted as labelled, not as an exclusion.",
    ]
    for variant in SPEC_VARIANTS:
        for spec in specs_for_variant(variant):
            if spec.zweck_groups:
                lines += _comment_paragraph(
                    "A W_ZWECK-defined group also WIDENS the purpose universe: under '%s' the %s "
                    "universe is W_ZWECK %s instead of the %s of the other variants, and the added "
                    "legs are labelled %s."
                    % (variant, spec.purpose_label, sorted(spec.zweck_values),
                       sorted(set(spec.zweck_values) - set(spec.zweck_group_codes)),
                       ", ".join(sorted(spec.zweck_groups))))
    lines += [
        "# Weights: W_GEW (MiD trip expansion weight).",
        "# spec_variant: which combination of the two subtype config keys",
        "#   (purpose_subtype_codeplan_sentinels, leisure_unspecified_subtype) a row was measured",
        "#   under. Rendered from SPEC_VARIANT_DESCRIPTIONS in the generating script, so this text",
        "#   and the comparison summary's cannot drift apart:",
    ]
    for variant in SPEC_VARIANTS:
        lines += _comment_paragraph("'%s' -- %s" % (variant, SPEC_VARIANT_DESCRIPTIONS[variant]))
    lines += [
        "# Exclusions: n_legs_raw=%d (raw Wege-file row count), n_legs_after_weekday_rbw=%d "
        "(%.2f%% kept;" % (diagnostics["n_legs_raw"], diagnostics["n_legs_after_weekday_rbw"],
                           100.0 * diagnostics["n_legs_after_weekday_rbw"]
                           / diagnostics["n_legs_raw"] if diagnostics["n_legs_raw"] else float("nan")),
        "#   the universe every count below is measured on), n_legs_invalid_weight=%d (missing or "
        "non-positive" % diagnostics["n_legs_invalid_weight"],
        "#   W_GEW; the extraction RAISES if this is not 0), n_values_coerced_to_nan=%s (per raw "
        "column, values" % (diagnostics["n_values_coerced_to_nan"],),
        "#   that became NaN under the numeric coercion of the raw text).",
        "# Coverage (labelled / sentinel / missing-distance legs per variant and purpose). A leg",
        "#   whose W_ZWECK puts it in a W_ZWECK-defined group (only leisure_unspecified today) is",
        "#   LABELLED by that group and is therefore not counted as a sentinel; the extraction",
        "#   WARNS whenever such a leg ALSO carries a valid W_ZWD group code (the 'overriding'",
        "#   count below), because the group ASSUMES those legs carry design sentinels only:",
    ]
    for key in sorted(key for key, value in diagnostics.items()
                      if isinstance(value, dict) and "n_purpose_legs" in value):
        stats = diagnostics[key]
        n_purpose = stats["n_purpose_legs"]
        lines.append(
            "#   %s: %d/%d legs labelled (%.2f%%), %d sentinel (%.2f%%);"
            % (key, stats["n_labelled"], n_purpose,
               100.0 * stats["n_labelled"] / n_purpose if n_purpose else float("nan"),
               stats["n_sentinel"],
               100.0 * stats["n_sentinel"] / n_purpose if n_purpose else float("nan")))
        lines.append("#     %d labelled legs carry a wegkm_imp missing-value code (>= %.0f) and are"
                     % (stats["n_missing_distance"], DISTANCE_MISSING_CODE_MIN))
        lines.append("#     excluded from the percentiles; %d were labelled by a W_ZWECK group "
                     "(%d overriding a valid" % (stats["n_by_zweck_group"],
                                                 stats["n_zweck_group_override"]))
        lines.append("#     W_ZWD group code).")
    lines += [
        "# Columns: purpose, spec_variant, group (the subtype group name the model uses),",
        "#   n_unweighted (labelled legs of the group), share_within_purpose (W_GEW share among the",
        "#   purpose's labelled legs; the shares of one purpose and variant sum to 1),",
        "#   km_p25/p50/p75 (W_GEW-weighted wegkm_imp percentiles in km, Hazen midpoint-CDF",
        "#   convention via srv_distance_targets.weighted_quantiles -- the same definition the SrV",
        "#   side uses, so the two are comparable), n_missing_distance (legs of the group excluded",
        "#   from the percentiles because wegkm_imp carries a missing-value code).",
        "# Rows: %d = %s"
        % (len(table), " + ".join("%d (%s)" % (int((table["spec_variant"] == variant).sum()),
                                               variant) for variant in SPEC_VARIANTS)),
        "#   -- one row per (spec variant, purpose, group). The variants differ in row count",
        "#   because only some of them define the W_ZWECK-defined leisure group.",
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
