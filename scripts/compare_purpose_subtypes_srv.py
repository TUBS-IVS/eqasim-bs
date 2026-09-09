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
                      "median_km_mid", "median_km_srv", "median_km_srv_components",
                      "candidate_for_reestimation"]

#: Stated verbatim in the summary so the flag's meaning cannot drift away from the code.
CANDIDATE_RULE = ("candidate_for_reestimation = (exactness == \"exact\") and "
                  "(abs(delta_pp) > %.0f)" % F.CANDIDATE_DELTA_PP_THRESHOLD)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("compare_purpose_subtypes_srv")


def _git_head() -> str:
    """Full git commit hash of HEAD, or 'unknown' -- never a fabricated value."""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                                text=True, check=True)
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
                share_srv, n_srv, components, median_srv, delta_pp = (
                    float("nan"), 0, "", float("nan"), float("nan"))

            candidate = bool(exactness == "exact" and np.isfinite(delta_pp)
                             and abs(delta_pp) > F.CANDIDATE_DELTA_PP_THRESHOLD)
            rows.append({
                "subtype_group": group, "purpose": mid_row["purpose"], "spec_variant": spec_variant,
                "srv_fine_codes": "|".join(str(code) for code in fine_codes),
                "exactness": exactness, "n_mid_unweighted": int(mid_row["n_unweighted"]),
                "share_mid": share_mid, "n_srv_unweighted": n_srv, "share_srv": share_srv,
                "delta_pp": delta_pp, "median_km_mid": float(mid_row["km_p50"]),
                "median_km_srv": median_srv, "median_km_srv_components": components,
                "candidate_for_reestimation": candidate,
            })
    return pd.DataFrame(rows, columns=COMPARISON_COLUMNS)


def render_summary(comparison: pd.DataFrame, srv_reference_path: Path, mid_reference_path: Path,
                   source_commit: str) -> str:
    """Human-readable, deliberately cautious summary of the comparison table."""
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
        "| code state (git rev-parse HEAD) | `%s` |" % source_commit,
        "| crosswalk | `braunschweig.calibration.srv_fine_purpose.SUBTYPE_TO_SRV_FINE` |",
        "| SrV weights | `GEWICHT_W_ZENSUS` (Zensus 2022 expansion, ADR-0055) |",
        "| MiD weights | `W_GEW` (trip expansion weight) |",
        "",
        "### Flag settings",
        "",
        "Both settings of the `purpose_subtype_codeplan_sentinels` config key are measured and",
        "reported side by side, as the `spec_variant` column:",
        "",
        "* `default`  -- `LEISURE_SPEC` / `OTHER_ERRAND_SPEC` (flag OFF): W_ZWD 799 stays in",
        "  `leisure_activity`, 699 stays in `other_errand_long`.",
        "* `codeplan` -- `LEISURE_SPEC_CODEPLAN` / `OTHER_ERRAND_SPEC_CODEPLAN` (flag ON): the two",
        "  no-detail codes are sentinels and leave estimation entirely.",
        "",
        "The shop split (`shop_daily` / `shop_non_daily`) has no codeplan variant, so its two rows",
        "are identical by construction. At this commit the key is not set in `configs/base_bs.yml`;",
        "the code default is `DEFAULT_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS = True`",
        "(`braunschweig/popsim/stage/config_keys.py`).",
        "",
        "### Candidate rule (verbatim)",
        "",
        "```",
        CANDIDATE_RULE,
        "```",
        "",
        "The flag marks a group whose two shares differ by more than",
        "%.0f percentage points AND whose crosswalk is graded `exact`, i.e. where the difference"
        % F.CANDIDATE_DELTA_PP_THRESHOLD,
        "cannot be explained away as a taxonomy mismatch. It is a pointer for a later decision,",
        "not a decision.",
        "",
        "## Comparison",
        "",
        "| subtype group | variant | purpose | SrV codes | exactness | n MiD | share MiD | n SrV |"
        " share SrV | delta pp | median km MiD | median km SrV |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    def _fmt(value, spec: str) -> str:
        return "n/a" if value is None or (isinstance(value, float) and not np.isfinite(value)) \
            else format(value, spec)

    def _cell(text: str) -> str:
        """A '|' inside a Markdown table cell would start a new column -- show it as ', '."""
        return str(text).replace("|", ", ")

    for _, row in comparison.iterrows():
        median_srv = _fmt(row["median_km_srv"], ".2f")
        if median_srv == "n/a" and row["median_km_srv_components"]:
            median_srv = "(%s)" % _cell(row["median_km_srv_components"])
        lines.append("| %s%s | %s | %s | %s | %s | %d | %s | %d | %s | %s | %s | %s |" % (
            row["subtype_group"], " **" if row["candidate_for_reestimation"] else "",
            row["spec_variant"], row["purpose"], _cell(row["srv_fine_codes"]) or "-",
            row["exactness"],
            int(row["n_mid_unweighted"]), _fmt(row["share_mid"], ".4f"),
            int(row["n_srv_unweighted"]), _fmt(row["share_srv"], ".4f"),
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
        lines.append("None: no `exact` crosswalk differs by more than %.0f pp."
                     % F.CANDIDATE_DELTA_PP_THRESHOLD)
    else:
        for _, row in flagged.iterrows():
            lines.append("* `%s` (%s): MiD %.4f vs SrV %.4f, delta %+.1f pp (n MiD %d, n SrV %d)."
                         % (row["subtype_group"], row["spec_variant"], row["share_mid"],
                            row["share_srv"], row["delta_pp"], int(row["n_mid_unweighted"]),
                            int(row["n_srv_unweighted"])))
    largest = comparison.dropna(subset=["delta_pp"]).reindex(
        comparison["delta_pp"].abs().sort_values(ascending=False).index).dropna(subset=["delta_pp"])
    lines += [
        "",
        "## Largest differences regardless of exactness",
        "",
        "Reported so that a large gap under an `approximate` crosswalk is visible rather than",
        "hidden by the flag rule; such a gap is NOT by itself a defect signal.",
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
        "   reported for completeness; only `exact` rows feed the candidate flag.",
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
                        help="Git commit hash to record (default: git rev-parse HEAD).")
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
        handle.write("# Code state: eqasim-bs %s, crosswalk "
                     "braunschweig.calibration.srv_fine_purpose.SUBTYPE_TO_SRV_FINE.\n"
                     % source_commit)
        handle.write("# Rule: %s\n" % CANDIDATE_RULE)
        handle.write("# MEASUREMENT ONLY -- not a validation and not a control target; see "
                     "summary.md for the caveats.\n")
        handle.write("# Generated by scripts/compare_purpose_subtypes_srv.py; regenerate there, "
                     "never edit.\n")
        comparison.to_csv(handle, index=False, lineterminator="\n", float_format="%.10g")
    logger.info("wrote %s (%d rows)", comparison_path, len(comparison))

    summary_path = args.out_dir / SUMMARY_FILE
    with open(summary_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_summary(comparison, args.srv_reference, args.mid_reference,
                                    source_commit))
    logger.info("wrote %s", summary_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
