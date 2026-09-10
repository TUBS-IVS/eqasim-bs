"""Compare the MiD W_ZWD subtype mix with the regional SrV fine-purpose mix (issue #242 Task 6).

Joins the two committed reference tables produced by the sibling extraction scripts

    eqasim-data/data/braunschweig/srv/srv2023_fine_purpose_reference.csv
        (regional SrV 2023 Braunschweig + RGB: share of each fine V_ZWECK code WITHIN its
         coarse purpose, GEWICHT_W_ZENSUS-weighted)
    eqasim-data/data/braunschweig/mid/mid2023_w_zwd_group_reference.csv
        (national MiD 2023: share of each W_ZWD subtype group among the LABELLED legs of its
         purpose, W_GEW-weighted, for both settings of purpose_subtype_codeplan_sentinels)

on ``braunschweig.calibration.srv_fine_purpose.SUBTYPE_TO_SRV_FINE`` and writes a comparison
table plus a human-readable summary to ``--out-dir``.

This is a MEASUREMENT, not a verdict: it says how far the national subtype mix the model
estimates on is from the regional fine-purpose mix, under an explicitly graded crosswalk. It
re-estimates nothing, changes no spec and no group, and it is NOT a validation -- neither table
is an observed control target for the synthetic population, and the two surveys differ in
taxonomy, universe and weighting (see the caveats the summary spells out).

Usage (eqasim env, from a worktree):
    python scripts/compare_purpose_subtypes_srv.py \
        --srv-reference eqasim-data/data/braunschweig/srv/srv2023_fine_purpose_reference.csv \
        --mid-reference eqasim-data/data/braunschweig/mid/mid2023_w_zwd_group_reference.csv \
        --out-dir eqasim-data/data/braunschweig/calibration/purpose_subtype_vs_srv_<date>

Deviation from the task brief's "--raw / --out-dir" script convention (deliberate): this script
takes the two REFERENCE paths instead of a raw directory, because it must not open a second,
divergent measurement path to the same numbers -- everything it reports is traceable to the two
committed files it names in the summary.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.calibration import srv_fine_purpose as F  # noqa: E402
from braunschweig.popsim.stage.config_keys import (  # noqa: E402
    DEFAULT_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS, KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS,
)

#: Name of the config-default constant, rendered into the summary beside its value so the summary
#: cannot drift away from the code the way hard-coded prose would.
_CONFIG_DEFAULT_NAME = "DEFAULT_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS"

SRV_REFERENCE_DEFAULT = (REPO / "eqasim-data" / "data" / "braunschweig" / "srv"
                         / F.FINE_PURPOSE_TABLE)
MID_REFERENCE_DEFAULT = (REPO / "eqasim-data" / "data" / "braunschweig" / "mid"
                         / "mid2023_w_zwd_group_reference.csv")
OUT_DIR_DEFAULT = (REPO / "eqasim-data" / "data" / "braunschweig" / "calibration"
                   / ("purpose_subtype_vs_srv_%s" % dt.date.today().isoformat()))
COMPARISON_FILE = "comparison.csv"
SUMMARY_FILE = "summary.md"

COMPARISON_COLUMNS = ["subtype_group", "purpose", "spec_variant", "srv_fine_codes", "exactness",
                      "n_mid_unweighted", "share_mid", "n_srv_unweighted", "share_srv", "delta_pp",
                      "share_mid_renormalised", "share_srv_renormalised", "delta_pp_renormalised",
                      "median_km_mid", "median_km_srv", "median_km_srv_components",
                      "candidate_for_reestimation", "delta_pp_comparable", "candidate_variants"]

#: Stated verbatim in the summary so the flag's meaning cannot drift away from the code.
CANDIDATE_RULE = ("candidate_for_reestimation = (exactness == \"exact\") and any over spec "
                  "variants of (abs(delta_pp_comparable) > %.0f); delta_pp_comparable = "
                  "delta_pp_renormalised where the purpose is asymmetric, else delta_pp"
                  % F.CANDIDATE_DELTA_PP_THRESHOLD)

#: A purpose's comparable mass may miss 1.0 by at most this much before the renormalised columns
#: are filled. Floating-point noise on a purpose whose comparable groups exhaust both sides must
#: not produce a spurious asymmetry reading.
MAPPED_MASS_TOLERANCE = 1e-9

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("compare_purpose_subtypes_srv")


def _git_head() -> str:
    """8-character git commit hash of HEAD, or 'unknown' -- never a fabricated value.

    Eight characters, like the sibling committed reference tables record.
    """
    try:
        result = subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], cwd=REPO,
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except Exception:
        return "unknown"


def read_reference_csv(path: Path) -> pd.DataFrame:
    """Read a committed reference table, skipping its ``#`` provenance header."""
    if not path.exists():
        raise FileNotFoundError(
            f"[compare_purpose_subtypes_srv] reference table not found: {path}. Generate it with "
            "scripts/extract_srv_fine_purpose_reference.py / scripts/extract_mid_w_zwd_groups.py "
            "first (both need the LOCAL raw microdata).")
    return pd.read_csv(path, comment="#")


def build_comparison(srv_reference: pd.DataFrame, mid_reference: pd.DataFrame) -> pd.DataFrame:
    """One row per (subtype group, MiD spec variant) with both shares and their difference.

    Parameters
    ----------
    srv_reference : DataFrame
        SrV fine-purpose reference (:data:`srv_fine_purpose.REFERENCE_COLUMNS`).
    mid_reference : DataFrame
        MiD W_ZWD group reference: ``purpose``, ``spec_variant``, ``group``, ``n_unweighted``,
        ``share_within_purpose``, ``km_p50``.

    Returns
    -------
    DataFrame
        :data:`COMPARISON_COLUMNS`. ``share_srv`` is the sum of the mapped fine codes'
        ``share_within_coarse``; ``delta_pp`` is ``100 * (share_mid - share_srv)``; an
        ``aggregate_only`` group (no SrV counterpart) carries NaN for both and is never flagged.

        ``median_km_srv`` is filled only where a group maps to exactly ONE fine code: a pooled
        median over several codes cannot be recomputed from committed percentile rows without the
        underlying observations, and inventing one (e.g. averaging two medians) would fabricate a
        number. The per-code medians are carried verbatim in ``median_km_srv_components`` instead,
        so nothing is lost.

        ``share_mid_renormalised`` / ``share_srv_renormalised`` / ``delta_pp_renormalised`` are the
        COMPARABLE-UNIVERSE reading of a purpose whose two sides do not cover the same activity
        mass: each side is divided by its own COMPARABLE mass, i.e. the mass of the rows graded
        :data:`srv_fine_purpose.COMPARABLE_EXACTNESS`. They are filled only for the comparable rows
        of a purpose whose comparable mass misses 1.0 on either side by more than
        :data:`MAPPED_MASS_TOLERANCE` (in this data: ``leisure``, where SrV code 18 "Andere
        Freizeitaktivitaet" is paired with nothing and MiD ``leisure_excursion`` has no SrV
        counterpart), and are NaN everywhere else -- for a purpose whose groups exhaust both sides
        the renormalisation is the identity and a filled column would only invite reading the same
        number twice.

        ``delta_pp_comparable`` is the delta the flag reads (the renormalised one where filled,
        else the raw one) and ``candidate_variants`` names the spec variants in which the group
        crosses the threshold; see :func:`_add_comparable_delta_and_flag`.

    Raises
    ------
    ValueError
        If a subtype group of :data:`SUBTYPE_TO_SRV_FINE` is missing from the MiD reference for
        some spec variant, if a mapped fine code is missing from the SrV reference, or if the MiD
        purpose disagrees with the coarse purpose of the mapped SrV codes.
    """
    srv = srv_reference.set_index("fine_code")
    rows = []
    for spec_variant, variant_rows in mid_reference.groupby("spec_variant", sort=True):
        mid = variant_rows.set_index("group")
        missing = sorted(set(F.SUBTYPE_TO_SRV_FINE) - set(mid.index))
        if missing:
            raise ValueError(
                f"[compare_purpose_subtypes_srv] MiD reference variant '{spec_variant}' has no row "
                f"for subtype group(s) {missing}; every group of SUBTYPE_TO_SRV_FINE must be "
                "measured, otherwise the comparison would silently omit it.")
        for group, (fine_codes, exactness) in F.SUBTYPE_TO_SRV_FINE.items():
            mid_row = mid.loc[group]
            unknown = [code for code in fine_codes if code not in srv.index]
            if unknown:
                raise ValueError(
                    f"[compare_purpose_subtypes_srv] SrV reference has no row for fine code(s) "
                    f"{unknown} required by subtype group '{group}'.")
            coarse = {F.coarse_of(code) for code in fine_codes}
            if coarse and coarse != {mid_row["purpose"]}:
                raise ValueError(
                    f"[compare_purpose_subtypes_srv] subtype group '{group}' is measured on MiD "
                    f"purpose '{mid_row['purpose']}' but maps to SrV coarse purpose(s) "
                    f"{sorted(coarse)}; the two shares would have different denominators.")

            share_mid = float(mid_row["share_within_purpose"])
            if fine_codes:
                share_srv = float(sum(float(srv.loc[code, "share_within_coarse"])
                                      for code in fine_codes))
                n_srv = int(sum(int(srv.loc[code, "n_unweighted"]) for code in fine_codes))
                components = "|".join("%d:%.3f" % (code, float(srv.loc[code, "gis_km_p50"]))
                                      for code in fine_codes)
                median_srv = (float(srv.loc[fine_codes[0], "gis_km_p50"])
                              if len(fine_codes) == 1 else float("nan"))
                delta_pp = 100.0 * (share_mid - share_srv)
            else:
                # An aggregate_only group has no SrV counterpart at all: n_srv_unweighted stays
                # EMPTY (NaN) rather than 0, because 0 would read as "SrV observed none of these
                # trips" instead of "SrV does not code this activity separately".
                share_srv, n_srv, components, median_srv, delta_pp = (
                    float("nan"), float("nan"), "", float("nan"), float("nan"))

            rows.append({
                "subtype_group": group, "purpose": mid_row["purpose"], "spec_variant": spec_variant,
                "srv_fine_codes": "|".join(str(code) for code in fine_codes),
                "exactness": exactness, "n_mid_unweighted": int(mid_row["n_unweighted"]),
                "share_mid": share_mid, "n_srv_unweighted": n_srv, "share_srv": share_srv,
                "delta_pp": delta_pp, "median_km_mid": float(mid_row["km_p50"]),
                "median_km_srv": median_srv, "median_km_srv_components": components,
            })
    comparison = _add_renormalised_columns(pd.DataFrame(rows, columns=COMPARISON_COLUMNS))
    return _add_comparable_delta_and_flag(comparison)


def _add_renormalised_columns(comparison: pd.DataFrame) -> pd.DataFrame:
    """Fill the comparable-universe columns for the asymmetric purposes only (spec 2.1.1).

    For each (spec variant, purpose) block the COMPARABLE mass is the sum of ``share_mid`` (resp.
    ``share_srv``) over the block's rows whose crosswalk grade is in
    :data:`srv_fine_purpose.COMPARABLE_EXACTNESS`, i.e. the activities BOTH surveys name
    concretely. Where both masses are 1.0 the renormalisation is the identity and the columns stay
    NaN; where they are not, each side is divided by its OWN comparable mass, so the two
    conditional distributions become comparable. Rows of any other grade are never renormalised --
    they are, by definition, unmapped mass on their own side, whether or not they carry an SrV fine
    code (a grade that has a code but no comparable counterpart must not enter the denominator).
    """
    comparison = comparison.copy()
    for column in ("share_mid_renormalised", "share_srv_renormalised", "delta_pp_renormalised"):
        comparison[column] = float("nan")
    comparable = comparison["exactness"].isin(F.COMPARABLE_EXACTNESS)
    for (variant, purpose), block in comparison[comparable].groupby(["spec_variant", "purpose"]):
        mid_mass, srv_mass = float(block["share_mid"].sum()), float(block["share_srv"].sum())
        symmetric = (abs(mid_mass - 1.0) <= MAPPED_MASS_TOLERANCE
                     and abs(srv_mass - 1.0) <= MAPPED_MASS_TOLERANCE)
        logger.info("%s/%s: comparable mass MiD %.4f, SrV %.4f -- %s", variant, purpose, mid_mass,
                    srv_mass, "symmetric, renormalisation is the identity (columns left empty)"
                    if symmetric else "ASYMMETRIC, renormalised columns filled")
        if symmetric or mid_mass <= 0 or srv_mass <= 0:
            continue
        share_mid = comparison.loc[block.index, "share_mid"] / mid_mass
        share_srv = comparison.loc[block.index, "share_srv"] / srv_mass
        comparison.loc[block.index, "share_mid_renormalised"] = share_mid
        comparison.loc[block.index, "share_srv_renormalised"] = share_srv
        comparison.loc[block.index, "delta_pp_renormalised"] = 100.0 * (share_mid - share_srv)
    return comparison


def _add_comparable_delta_and_flag(comparison: pd.DataFrame) -> pd.DataFrame:
    """delta_pp_comparable feeds the flag; the flag is GROUP-level across spec variants.

    A group is a candidate when its crosswalk is ``exact`` and ``abs(delta_pp_comparable)`` exceeds
    ``CANDIDATE_DELTA_PP_THRESHOLD`` in at least one spec variant; every row of the group carries the
    flag and the ``|``-joined list of crossing variants, so a reader of one variant's row still
    sees that the group crossed elsewhere (spec 2.1.3).
    """
    comparison = comparison.copy()
    comparison["delta_pp_comparable"] = comparison["delta_pp_renormalised"].where(
        comparison["delta_pp_renormalised"].notna(), comparison["delta_pp"])
    # Which reading each row's flag actually rests on, as a rate (CLAUDE.md "Fallback
    # transparency"): the raw delta is the intended value for a symmetric purpose, but a run in
    # which NO row reaches the renormalised reading would mean the comparable universe never
    # applied, which the log must show rather than hide.
    n_rows = len(comparison)
    n_renormalised = int(comparison["delta_pp_renormalised"].notna().sum())
    n_defined = int(comparison["delta_pp_comparable"].notna().sum())
    logger.info("delta_pp_comparable: %d/%d rows renormalised (asymmetric purposes), %d raw "
                "(symmetric purposes), %d undefined (no SrV counterpart)", n_renormalised, n_rows,
                n_defined - n_renormalised, n_rows - n_defined)
    comparison["candidate_for_reestimation"] = False
    comparison["candidate_variants"] = ""
    exact = comparison["exactness"] == "exact"
    crossing = exact & (comparison["delta_pp_comparable"].abs() > F.CANDIDATE_DELTA_PP_THRESHOLD)
    for group, block in comparison[crossing].groupby("subtype_group"):
        variants = "|".join(sorted(block["spec_variant"].astype(str).unique()))
        rows = comparison["subtype_group"] == group
        comparison.loc[rows, "candidate_for_reestimation"] = True
        comparison.loc[rows, "candidate_variants"] = variants
        logger.info("candidate %s: comparable delta beyond %.0f pp in variant(s) %s", group,
                    F.CANDIDATE_DELTA_PP_THRESHOLD, variants)
    return comparison


def _fmt(value, spec: str) -> str:
    """Format a number for the summary, or 'n/a' when it is missing -- never a substituted 0."""
    return "n/a" if value is None or (isinstance(value, float) and not np.isfinite(value)) \
        else format(value, spec)


def _cell(text) -> str:
    """A '|' inside a Markdown table cell would start a new column -- show it as ', '."""
    return str(text).replace("|", ", ")


def _join(names) -> str:
    return ", ".join("`%s`" % name for name in names)


def _purposes_by_mapped_mass(comparison: pd.DataFrame) -> tuple:
    """Split the purposes into (symmetric, asymmetric) by whether renormalisation changes them.

    A purpose is SYMMETRIC when its comparable rows exhaust the whole mass on both sides, which
    :func:`_add_renormalised_columns` records by leaving the renormalised columns empty for it.
    """
    symmetric, asymmetric = set(), set()
    for purpose, block in comparison.groupby("purpose"):
        (asymmetric if block["delta_pp_renormalised"].notna().any() else symmetric).add(purpose)
    return sorted(symmetric), sorted(asymmetric)


def _render_candidate_bullets(flagged: pd.DataFrame) -> list:
    """One bullet per FLAGGED GROUP (spec 2.1.3): every variant's comparable delta, then the raw.

    The flag is group-level, so the unit of the bullet is the group, not the row: it reports the
    comparable delta of EVERY spec variant of the group, names the variant(s) that actually cross
    the threshold and repeats the raw deltas in the same variant order as information. The
    crossing variants are listed first because they are the reason the group carries the flag.
    Generated from the comparison table, so the numbers cannot drift away from the committed CSV.
    """
    lines = []
    for group, block in flagged.groupby("subtype_group"):
        crossing = [name for name in str(block["candidate_variants"].iloc[0]).split("|") if name]
        ordered = sorted(block.itertuples(index=False),
                         key=lambda row: (row.spec_variant not in crossing, row.spec_variant))
        comparable = ", ".join("%s %s pp" % (row.spec_variant,
                                             _fmt(row.delta_pp_comparable, "+.1f"))
                               for row in ordered)
        raw = " / ".join(_fmt(row.delta_pp, "+.1f") for row in ordered)
        lines.append("* `%s` (%s): comparable delta %s; crossing variant(s): %s. Raw deltas %s pp."
                     % (group, block["exactness"].iloc[0], comparable,
                        _join(crossing) or "(none)", raw))
    return lines


def _render_sensitivity_section(comparison: pd.DataFrame) -> list:
    """The comparable-universe table for the asymmetric purposes (empty list if there is none).

    Generated from the comparison table itself rather than written by hand, so the renormalised
    deltas cannot drift away from the committed CSV.
    """
    rows = comparison[comparison["delta_pp_renormalised"].notna()]
    if rows.empty:
        return []
    _, asymmetric = _purposes_by_mapped_mass(comparison)
    lines = [
        "",
        "## Comparable-universe reading (feeds the flag)",
        "",
        "Where a purpose is ASYMMETRIC -- the two surveys do not name the same activity mass --",
        "the raw shares are conditional on different universes and cannot be differenced as they",
        "stand; here that is %s. The comparable reading divides each side by its OWN"
        % _join(asymmetric),
        "comparable mass -- the mass of the rows graded %s -- and it is THIS reading"
        % _join(F.COMPARABLE_EXACTNESS),
        "that feeds the candidate flag (`delta_pp_comparable` is the renormalised delta here and",
        "the raw delta where a purpose is symmetric and the renormalisation is the identity). The",
        "raw shares are kept beside it as information: they answer \"what fraction of the",
        "purpose's trips is this group?\", the comparable ones answer \"among the trips the two",
        "surveys pair up, what fraction is this group?\".",
        "",
        "| subtype group | variant | exactness | share MiD | share SrV | delta pp | share MiD"
        " renorm. | share SrV renorm. | delta pp renorm. |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in rows.iterrows():
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            row["subtype_group"], row["spec_variant"], row["exactness"],
            _fmt(row["share_mid"], ".4f"), _fmt(row["share_srv"], ".4f"),
            _fmt(row["delta_pp"], "+.1f"), _fmt(row["share_mid_renormalised"], ".4f"),
            _fmt(row["share_srv_renormalised"], ".4f"),
            _fmt(row["delta_pp_renormalised"], "+.1f")))

    crossings = rows[(rows["exactness"] == "exact")
                     & (rows["delta_pp"].abs() <= F.CANDIDATE_DELTA_PP_THRESHOLD)
                     & (rows["delta_pp_renormalised"].abs() > F.CANDIDATE_DELTA_PP_THRESHOLD)]
    lines.append("")
    if crossings.empty:
        lines.append("No row changes side of the %.0f pp threshold between the two readings."
                     % F.CANDIDATE_DELTA_PP_THRESHOLD)
    else:
        lines += [
            "**Rows whose flag differs between the two readings.** The following `exact` row(s) are",
            "within the %.0f pp threshold on the RAW delta and beyond it on the comparable one, so"
            % F.CANDIDATE_DELTA_PP_THRESHOLD,
            "the raw reading alone would have missed them; the committed",
            "`candidate_for_reestimation` flag reads the comparable delta, as the rule above says.",
            "",
        ]
        for _, row in crossings.iterrows():
            lines.append("* `%s` (%s): %+.1f pp raw -> %+.1f pp comparable."
                         % (row["subtype_group"], row["spec_variant"], row["delta_pp"],
                            row["delta_pp_renormalised"]))
    return lines


def render_summary(comparison: pd.DataFrame, srv_reference_path: Path, mid_reference_path: Path,
                   source_commit: str, *, source_commit_from_flag: bool = False) -> str:
    """Human-readable, deliberately cautious summary of the comparison table.

    ``source_commit_from_flag`` records HOW the code state was obtained, so a reader can tell a
    value the caller asserted with ``--source-commit`` from one this script read out of git.
    """
    commit_source_label = "--source-commit" if source_commit_from_flag else "git rev-parse HEAD"
    lines = [
        "# MiD W_ZWD subtype mix vs SrV 2023 fine-purpose mix (issue #242, sub-project C)",
        "",
        "Generated by `scripts/compare_purpose_subtypes_srv.py` on %s." % dt.date.today().isoformat(),
        "",
        "## What this is (and is not)",
        "",
        "This is a MEASUREMENT of the distance between two survey-derived purpose mixes. It is",
        "NOT a validation: neither table is an observed control target for the synthetic",
        "population, no model output is compared here, and nothing is re-estimated. A large",
        "difference is a SIGNAL to investigate, not evidence that the model is wrong -- the two",
        "sources differ in taxonomy, universe, region and weighting, and part of any difference",
        "is a taxonomy artefact rather than a regional one (which is exactly what the",
        "`exactness` grade tries to separate).",
        "",
        "## Provenance",
        "",
        "| item | value |",
        "| --- | --- |",
        "| SrV reference | `%s` |" % srv_reference_path.as_posix(),
        "| MiD reference | `%s` |" % mid_reference_path.as_posix(),
        "| code state (%s) | `%s` |" % (commit_source_label, source_commit),
        "| crosswalk | `braunschweig.calibration.srv_fine_purpose.SUBTYPE_TO_SRV_FINE` |",
        "| SrV weights | `GEWICHT_W_ZENSUS` (Zensus 2022 expansion, ADR-0055) |",
        "| MiD weights | `W_GEW` (trip expansion weight) |",
        "",
        "The code state above is the commit that moved the candidate rule onto the comparable",
        "universe and made the flag group-level across the spec variants (issue #242 item 8, owner",
        "decision 2026-09-10). Both reference tables are unchanged, so every measured data row of",
        "`comparison.csv` is unchanged; what changed is the meaning of",
        "`candidate_for_reestimation` and the two added columns `delta_pp_comparable` and",
        "`candidate_variants`.",
        "",
        "### Flag settings",
        "",
        "Both settings of the `%s` config key are measured and"
        % KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS,
        "reported side by side, as the `spec_variant` column:",
        "",
        "* `default`  -- `LEISURE_SPEC` / `OTHER_ERRAND_SPEC` (flag OFF): W_ZWD 799 stays in",
        "  `leisure_activity`, 699 stays in `other_errand_long`.",
        "* `codeplan` -- `LEISURE_SPEC_CODEPLAN` / `OTHER_ERRAND_SPEC_CODEPLAN` (flag ON): the two",
        "  no-detail codes are sentinels and leave estimation entirely.",
        "",
        "The shop split (`shop_daily` / `shop_non_daily`) has no codeplan variant, so its two rows",
        "are identical by construction. The code default read out of",
        "`braunschweig/popsim/stage/config_keys.py` at generation time is",
        "`%s = %r`, i.e. the `codeplan` rows describe the"
        % (_CONFIG_DEFAULT_NAME, DEFAULT_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS),
        "default-configured behaviour; the `default` rows describe the flag switched off.",
        "",
        "### Candidate rule (verbatim)",
        "",
        "```",
        CANDIDATE_RULE,
        "```",
        "",
        "The flag marks a group whose two shares differ by more than",
        "%.0f percentage points on the COMPARABLE universe -- each side renormalised to the mass"
        % F.CANDIDATE_DELTA_PP_THRESHOLD,
        "of the rows both surveys name concretely (%s) -- AND whose crosswalk is graded"
        % _join(F.COMPARABLE_EXACTNESS),
        "`exact`, i.e. where the difference cannot be explained away as a taxonomy mismatch.",
        "The flag is GROUP-level across the spec variants: a group that crosses the threshold in",
        "at least one variant is flagged on every one of its rows and `candidate_variants` names",
        "the crossing variants, because the group is the unit of a possible re-estimation and a",
        "1 pp difference between the variants must not give two answers for one group.",
        "",
        "ASSUMPTION: the %.0f pp threshold is a practical relevance line, not a derived bound. The"
        % F.CANDIDATE_DELTA_PP_THRESHOLD,
        "sampling standard error of an SrV within-leisure share with n ~ 1,900 is about 1 pp, so",
        "the threshold decides relevance, not significance; and it measures SHARES, while the",
        "model consequence is DISTANCE. It is a pointer for a later decision, not a decision.",
        "",
        "## Comparison",
        "",
        "| subtype group | variant | purpose | SrV codes | exactness | n MiD | share MiD | n SrV |"
        " share SrV | delta pp | median km MiD | median km SrV |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for _, row in comparison.iterrows():
        median_srv = _fmt(row["median_km_srv"], ".2f")
        if median_srv == "n/a" and row["median_km_srv_components"]:
            median_srv = "(%s)" % _cell(row["median_km_srv_components"])
        lines.append("| %s%s | %s | %s | %s | %s | %d | %s | %s | %s | %s | %s | %s |" % (
            row["subtype_group"], " **" if row["candidate_for_reestimation"] else "",
            row["spec_variant"], row["purpose"], _cell(row["srv_fine_codes"]) or "-",
            row["exactness"],
            int(row["n_mid_unweighted"]), _fmt(row["share_mid"], ".4f"),
            _fmt(row["n_srv_unweighted"], ".0f"), _fmt(row["share_srv"], ".4f"),
            _fmt(row["delta_pp"], "+.1f"), _fmt(row["median_km_mid"], ".2f"), median_srv))

    flagged = comparison[comparison["candidate_for_reestimation"]]
    lines += [
        "",
        "`**` marks a `candidate_for_reestimation` row. A `median km SrV` cell in parentheses",
        "lists the per-fine-code medians because a pooled median over several codes cannot be",
        "recomputed from committed percentile rows (see `median_km_srv_components`).",
        "",
        "## Candidates flagged",
        "",
    ]
    if len(flagged) == 0:
        symmetric, asymmetric = _purposes_by_mapped_mass(comparison)
        lines += [
            "None under the rule above: no `exact` crosswalk differs by more than %.0f pp in any"
            % F.CANDIDATE_DELTA_PP_THRESHOLD,
            "spec variant.",
            "",
            "That headline already covers the purposes whose crosswalk does NOT pair up the whole",
            "mass on both sides -- here %s, against the symmetric %s -- because the flag reads the"
            % (_join(asymmetric) or "(none)", _join(symmetric) or "(none)"),
            "comparable delta; the section below shows both readings side by side.",
        ]
    else:
        lines += _render_candidate_bullets(flagged)
        lines += [
            "",
            "A candidate triggers no re-estimation here: the owner decides from the arm-B",
            "measurement of the REALISED share (feature record `w_zwd_codeplan_sentinels.yml`).",
        ]

    lines += _render_sensitivity_section(comparison)

    largest = comparison.dropna(subset=["delta_pp"]).reindex(
        comparison["delta_pp"].abs().sort_values(ascending=False).index).dropna(subset=["delta_pp"])
    lines += [
        "",
        "## Largest differences regardless of exactness",
        "",
        "Reported so that a large gap under an `approximate` crosswalk is visible rather than",
        "hidden by the flag rule; such a gap is NOT by itself a defect signal. Raw shares.",
        "",
    ]
    for _, row in largest.head(3).iterrows():
        lines.append("* `%s` (%s, %s): MiD %.4f vs SrV %.4f, delta %+.1f pp."
                     % (row["subtype_group"], row["spec_variant"], row["exactness"],
                        row["share_mid"], row["share_srv"], row["delta_pp"]))
    lines += [
        "",
        "## Caveats that limit how far these numbers carry",
        "",
        "1. **Different denominators inside `leisure`.** The SrV leisure share is taken over the",
        "   fine codes 13-18, which include 18 \"Andere Freizeitaktivitaet\" -- a residual that the",
        "   crosswalk maps to no subtype group. The MiD leisure share is taken over the LABELLED",
        "   legs of W_ZWECK 7, which include `leisure_excursion` -- a group SrV codes nowhere.",
        "   The four leisure `share_srv` values therefore do not sum to 1, and neither mix is a",
        "   subset of the other.",
        "2. **The MiD share is conditional on being labelled.** A large share of MiD legs carry a",
        "   design sentinel instead of a W_ZWD detail code (PAPI interview, child under 14); those",
        "   legs are excluded from the denominator, exactly as the estimation excludes them. The",
        "   labelled share per purpose is reported in the MiD reference file's header.",
        "3. **National vs regional.** MiD 2023 is the national survey the subtype models are",
        "   estimated on; SrV 2023 here is the Braunschweig + RGB delivery only. A difference",
        "   mixes a regional effect with a survey-instrument effect and cannot be attributed to",
        "   either from this table alone.",
        "4. **`approximate` and `aggregate_only` rows are not evidence of a defect.** They are",
        "   reported for completeness; only `exact` rows feed the candidate flag, on the",
        "   comparable delta.",
        "5. **`other_errand_short` is graded `approximate`, not `exact`** (issue #242 Task 6",
        "   review, ruling C-R16), for two independent reasons: the labels overlap the other",
        "   member of the pair -- MiD W_ZWD 602 \"Behoerde, Bank, Post\" feeds",
        "   `other_errand_short` while SrV 11 \"Dienstleistungseinrichtung (z. B. Post, Bank,",
        "   Friseur, Apotheke)\" feeds `other_errand_long` -- and with exactly two groups per side",
        "   the two shares are complements, so `delta_short == -delta_long` identically and the",
        "   pair cannot carry two different grades for one and the same number.",
        "6. **The two median distances rest on different coverage.** The SrV percentiles use only",
        "   trips with a computable GIS length (the SrV reference file's header states the exact",
        "   rate; it is well below 100 %), while `wegkm_imp` is present for every labelled MiD",
        "   leg of this delivery. A median difference therefore also carries whatever the",
        "   GIS-computability selection does, which this table cannot separate.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--srv-reference", type=Path, default=SRV_REFERENCE_DEFAULT,
                        help="Committed SrV fine-purpose reference CSV.")
    parser.add_argument("--mid-reference", type=Path, default=MID_REFERENCE_DEFAULT,
                        help="Committed MiD W_ZWD group reference CSV.")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR_DEFAULT,
                        help="Directory to write comparison.csv and summary.md into.")
    parser.add_argument("--source-commit", type=str, default=None,
                        help="Git commit hash of the code state to record (default: the 8-character "
                             "git rev-parse HEAD of this repository).")
    args = parser.parse_args(argv)

    srv_reference = read_reference_csv(args.srv_reference)
    mid_reference = read_reference_csv(args.mid_reference)
    logger.info("read %d SrV fine-purpose rows and %d MiD group rows", len(srv_reference),
                len(mid_reference))

    comparison = build_comparison(srv_reference, mid_reference)
    source_commit = args.source_commit or _git_head()
    n_flagged = int(comparison["candidate_for_reestimation"].sum())
    logger.info("%d/%d comparison rows flagged as candidate_for_reestimation (%s)", n_flagged,
                len(comparison), CANDIDATE_RULE)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = args.out_dir / COMPARISON_FILE
    with open(comparison_path, "w", encoding="utf-8", newline="") as handle:
        handle.write("# Comparison of the MiD W_ZWD subtype mix with the SrV 2023 fine-purpose "
                     "mix (issue #242 Task 6).\n")
        handle.write("# Sources: %s ; %s\n" % (args.srv_reference.as_posix(),
                                               args.mid_reference.as_posix()))
        handle.write("# Code state: eqasim-bs %s (the commit that moved the candidate rule onto "
                     "the comparable universe\n"
                     "#   and made the flag group-level), crosswalk\n"
                     "#   braunschweig.calibration.srv_fine_purpose.SUBTYPE_TO_SRV_FINE. Both "
                     "reference tables are\n"
                     "#   unchanged, so every measured data row is unchanged; what changed is the "
                     "meaning of\n"
                     "#   candidate_for_reestimation and the two added comparable-universe "
                     "columns.\n" % source_commit)
        handle.write("# Rule: %s\n" % CANDIDATE_RULE)
        handle.write("# Renormalised columns (share_mid_renormalised, share_srv_renormalised, "
                     "delta_pp_renormalised): each\n"
                     "#   side divided by its own COMPARABLE mass (the mass of the rows graded %s).\n"
                     "#   Filled ONLY for the comparable rows of a purpose whose comparable mass "
                     "does not cover the whole\n"
                     "#   mass on both sides (here: leisure); EMPTY elsewhere, where the "
                     "renormalisation is the identity.\n"
                     "#   n_srv_unweighted is EMPTY for an aggregate_only row, because SrV does "
                     "not code that activity\n"
                     "#   separately at all (never 0, which would read as 'none observed').\n"
                     % " + ".join(F.COMPARABLE_EXACTNESS))
        handle.write("# delta_pp_comparable / candidate_variants: delta_pp_comparable is the delta "
                     "the flag reads --\n"
                     "#   delta_pp_renormalised where the purpose is asymmetric, else delta_pp. "
                     "candidate_variants lists\n"
                     "#   the spec variant(s) in which the group crosses the threshold "
                     "('|'-joined, EMPTY when none);\n"
                     "#   the flag is GROUP-level, so every row of a crossing group carries both.\n")
        handle.write("# MEASUREMENT ONLY -- not a validation and not a control target; see "
                     "summary.md for the caveats.\n")
        handle.write("# Generated by scripts/compare_purpose_subtypes_srv.py; regenerate there, "
                     "never edit.\n")
        comparison.to_csv(handle, index=False, lineterminator="\n", float_format="%.10g")
    logger.info("wrote %s (%d rows)", comparison_path, len(comparison))

    summary_path = args.out_dir / SUMMARY_FILE
    with open(summary_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_summary(comparison, args.srv_reference, args.mid_reference,
                                    source_commit,
                                    source_commit_from_flag=args.source_commit is not None))
    logger.info("wrote %s", summary_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
