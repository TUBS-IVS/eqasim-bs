"""Derive the MiD 2023 escort W_ZWECK active/passive split (issue #256).

MiD codes escort two-sidedly: W_ZWECK 6 = active Bringen/Holen, W_ZWECK 13 =
the escorted person's own (passive) leg -- 100% minors, folded into Begleitung
by MiD's own derivations, hence contained in the published W1 8% and the W12
length profile. With escort_passive_education ON the model's escort purpose is
ACTIVE-ONLY, so validation needs the split to derive apples-to-apples
references:

  begleitung_active_ref  = W1_begleitung * share_weighted[code_6]
  education_adjusted_ref = W1_ausbildung + W1_begleitung * share_weighted[code_13]

and the code_6 rows provide the active-only length profile (bands in the W12
column convention) for the escort distance comparison.

With escort_passive_from_adult ON (issue #372, ADR-0112) only PART of the passive
mass is still education -- a paired child follows the accompanying adult's purpose,
and only an adult ACTIVE escort leg (or no pairing at all) leaves the child on the
escort_passive_education rule. The code_13 row therefore also carries
code_13_to_education_share_under_pairing, and the reference becomes

  education_adjusted_ref = W1_ausbildung
      + W1_begleitung * share_weighted[code_13] * code_13_to_education_share_under_pairing

Input:  eqasim-data/data/braunschweig/popsim/mid2023_raw/MiD2023_Wege.csv
        (LOCAL-only raw; comma-separated; W_ZWECK, W_GEW, wegkm_imp plus
        PAIRING_COLUMNS_NEEDED for the pairing-derived column)
Output: eqasim-data/data/braunschweig/mid/mid2023_escort_w_zweck_split.csv
        (committed pinned reference; regenerate here, never edit)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.derive_escort_location_weights import _sniff_separator, weighted_median

REPO = Path(__file__).resolve().parents[1]
DEFAULT_WEGE_PATH = (REPO / "eqasim-data" / "data" / "braunschweig" / "popsim"
                     / "mid2023_raw" / "MiD2023_Wege.csv")
DEFAULT_OUTPUT_PATH = (REPO / "eqasim-data" / "data" / "braunschweig" / "mid"
                       / "mid2023_escort_w_zweck_split.csv")

ESCORT_CODES = (6, 13)
#: Extra raw MiD Wege columns the passive-escort pairing needs on top of the three the
#: active/passive split itself reads (issue #372): the household/person/leg keys, the departure
#: time and the member's age (``escort_pairing.REQUIRED_COLUMNS``).
PAIRING_COLUMNS_NEEDED = ("H_ID", "P_ID", "W_ID", "W_SZS", "W_SZM", "HP_ALTER")
#: Extra raw MiD Wege columns the two TRIP-BUILD leg filters need on top of the pairing's own
#: (``trips.rbw_leg_mask`` / ``trips.leading_arrive_home_leg_index``).
LEG_FILTER_COLUMNS_NEEDED = ("W_RBW", "W_SO1")
#: Name of the EDUCATION member of the fold below. It keeps this longer, non-templated name
#: because it was committed first (issue #372, ADR-0112) and renaming a published reference
#: column buys nothing; ``trip_coherence.load_passive_purpose_fold`` reads it through the same
#: ``code_13_to_<purpose>_share_under_pairing`` pattern as every other member.
PASSIVE_EDUCATION_SHARE_COLUMN = "code_13_to_education_share_under_pairing"
#: Column name for each member of the passive-leg purpose fold (fix round 1, ruling C-R11).
PASSIVE_FOLD_COLUMN_TEMPLATE = "code_13_to_{purpose}_share_under_pairing"
#: The trip-build flag values every committed number below is derived under. Written into the CSV
#: header so a reader never has to guess which configuration the reference describes; they are
#: the PRODUCTION values (configs/base_bs.yml, plus the trips_stage defaults).
DERIVATION_FLAGS = {
    "escort_purpose": True,
    "escort_passive_education": True,
    "escort_passive_from_adult": True,
    "w_zweck_10_as_leisure": True,
    "exclude_rbw_legs": True,
    "drop_leading_arrive_home_leg": True,
}


def passive_fold_purposes() -> tuple:
    """The eqasim purposes a passive escort leg can receive, DERIVED from the model's own tables.

    The destinations ``trips.PASSIVE_PURPOSE_BY_ADULT_W_ZWECK`` can produce, plus the two the
    passive rule itself produces (``education`` under ``escort_passive_education``, ``escort``
    otherwise) and ``trips.DEFAULT_PURPOSE`` for the unknown-code fallback. Sorted, so the
    committed column order is deterministic. Derived rather than hardcoded: a future edit to the
    mapping table must not silently leave a purpose out of the committed fold.
    """
    from braunschweig.popsim import trips
    return tuple(sorted(set(trips.PASSIVE_PURPOSE_BY_ADULT_W_ZWECK.values())
                        | {"education", "escort", trips.DEFAULT_PURPOSE}))
# Band edges/columns mirror the committed mid2023_W12_triplength_by_purpose.csv.
BAND_EDGES = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, float("inf")]
BAND_COLUMNS = ["d_unter_0_5km", "d_0_5_1km", "d_1_2km", "d_2_5km", "d_5_10km",
                "d_10_20km", "d_20_50km", "d_50_100km", "d_100km_plus"]


def _band_shares_pct(length_km: pd.Series, weights: pd.Series) -> pd.Series:
    """Weighted length-band shares (%), returned as a ``pd.Series`` indexed by
    ``BAND_COLUMNS``.

    ``pd.cut`` is given ``labels=BAND_COLUMNS`` directly so each band's name
    travels with its categorical bucket through the groupby, instead of the
    caller having to line up a positional result list with ``BAND_COLUMNS``
    by relying on the post-cut Interval categories happening to sort in the
    same ascending order as ``BAND_COLUMNS`` (D12, deferred review). The
    final ``.reindex(BAND_COLUMNS)`` fixes the output order explicitly.
    """
    bins = pd.cut(np.asarray(length_km, dtype=float), BAND_EDGES, right=False,
                  labels=BAND_COLUMNS)
    share = (pd.DataFrame({"b": bins, "w": np.asarray(weights, dtype=float)})
             .groupby("b", observed=False)["w"].sum())
    share = (share / share.sum()).reindex(BAND_COLUMNS)
    return 100.0 * share.astype(float)


def derive_split(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Weighted active/passive split + per-code length profile.

    share_weighted is computed over ALL escort legs (W_GEW); the length
    statistics (mean/median/bands) use only legs with a usable wegkm_imp
    (numeric, >= 0, < 1000 -- mirroring the #257 coherence-gate filter), with
    the weighted coverage reported in stats.

    Precondition: W_ZWECK must already be numeric (main() coerces; direct
    callers must do the same).
    """
    escort = df[df["W_ZWECK"].isin(ESCORT_CODES)].copy()
    if len(escort) == 0:
        raise ValueError("[derive_escort_w_zweck_split] no escort legs "
                         f"(W_ZWECK in {list(ESCORT_CODES)}) found.")
    w_gew_coerced = pd.to_numeric(escort["W_GEW"], errors="coerce")
    weight_coercion_failures = int((escort["W_GEW"].notna() & w_gew_coerced.isna()).sum())
    escort["W_GEW"] = w_gew_coerced.fillna(0.0)
    escort["wegkm_imp"] = pd.to_numeric(escort["wegkm_imp"], errors="coerce")
    total_weight = float(escort["W_GEW"].sum())
    if total_weight == 0:
        raise ValueError(
            "[derive_escort_w_zweck_split] total escort weight is zero after "
            "coercion; check W_GEW parsing (garbage or all-zero weights)."
        )
    length_ok = escort["wegkm_imp"].notna() & (escort["wegkm_imp"] >= 0) \
        & (escort["wegkm_imp"] < 1000.0)
    coverage = float(escort.loc[length_ok, "W_GEW"].sum() / total_weight) if total_weight else 0.0

    rows = []
    for label, sub in (("code_6", escort[escort["W_ZWECK"] == 6]),
                       ("code_13", escort[escort["W_ZWECK"] == 13]),
                       ("both", escort)):
        sub_ok = sub[length_ok.reindex(sub.index, fill_value=False)]
        row = {
            "w_zweck": label,
            "n_legs_unweighted": int(len(sub)),
            "share_weighted": round(float(sub["W_GEW"].sum() / total_weight), 4)
                if total_weight else float("nan"),
            "mean_km": round(float(np.average(sub_ok["wegkm_imp"],
                                              weights=sub_ok["W_GEW"])), 4)
                if len(sub_ok) else float("nan"),
            "median_km": round(weighted_median(sub_ok["wegkm_imp"],
                                               sub_ok["W_GEW"]), 4)
                if len(sub_ok) else float("nan"),
        }
        bands = _band_shares_pct(sub_ok["wegkm_imp"], sub_ok["W_GEW"]) \
            if len(sub_ok) else pd.Series(float("nan"), index=BAND_COLUMNS)
        row.update({c: round(float(v), 2) for c, v in bands.items()})
        rows.append(row)

    table = pd.DataFrame(rows, columns=["w_zweck", "n_legs_unweighted",
                                        "share_weighted", "mean_km", "median_km",
                                        *BAND_COLUMNS])
    stats = {"length_coverage_weighted": coverage,
             "n_escort_legs": int(len(escort)),
             "weight_coercion_failures": weight_coercion_failures}
    return table, stats


def derive_passive_education_share(
    df: pd.DataFrame, *, max_gap_minutes: float | None = None,
    exclude_rbw_legs: bool = True, drop_leading_arrive_home_leg: bool = True,
) -> tuple[float, dict]:
    """W_GEW purpose fold of the passive escort legs under the pairing (#372, fix round 1).

    With ``escort_passive_from_adult`` on, a W_ZWECK-13 leg is realised as ``education`` by
    ``trips.map_purpose`` in exactly two cases: it is PAIRED with an adult leg that is itself an
    ACTIVE escort leg (``trips.ADULT_ESCORT_W_ZWECK``, the child really is being brought to their
    own Kita/school), or it stays UNPAIRED and therefore keeps the ``escort_passive_education``
    relabel. Every other paired leg follows the adult onto shop / home / leisure / other.

    Everything is computed by running the REAL pairing
    (``escort_pairing.pair_passive_legs``) and the REAL purpose derivation
    (``trips.passive_purpose_for_pairs``), never by re-deriving the rules here, so the committed
    reference cannot drift away from what the model does. For the same reason the legs are first
    reduced to the ones the trip build KEEPS (``trips.legs_kept_by_the_trip_build``): production
    drops the rbW summary legs and the leading arrive-home leg BEFORE ``map_purpose`` pairs, so a
    reference derived on the raw table would describe a universe no run ever has (fix round 1,
    IMPORTANT 3).

    ASSUMPTIONS, all disclosed in the committed CSV header: ``escort_passive_education`` and
    ``w_zweck_10_as_leisure`` are taken as True (their production values) when the paired adult's
    code is turned into the child's purpose. ``escort_passive_education`` decides what an adult
    ACTIVE escort leg gives the child (education vs escort) and therefore moves mass between two
    fold members; ``w_zweck_10_as_leisure`` moves adult code-10 mass between leisure and other.
    Neither can change the education total for any other adult code.

    Args:
        df: MiD Wege with ``W_ZWECK``, ``W_GEW``, PAIRING_COLUMNS_NEEDED and (for the active
            filters) LEG_FILTER_COLUMNS_NEEDED. ``W_ZWECK`` must already be numeric (``main()``
            coerces).
        max_gap_minutes: the pairing window in minutes; ``None`` uses the model's own default.
        exclude_rbw_legs / drop_leading_arrive_home_leg: the trip build's leg-drop flags,
            defaulting to their PRODUCTION values, applied through the shared trips.py helpers.

    Returns:
        ``(share, stats)``. ``share`` is the W_GEW-weighted share of code-13 legs realised as
        education, in [0, 1]. ``stats`` carries ``n_passive`` / ``n_passive_raw`` (after and
        before the leg filters), ``n_paired``, ``share_paired``, the two education components
        ``share_paired_to_active_escort`` / ``share_unpaired`` (so the headline number can be
        read, not just believed), ``fold`` (purpose -> W_GEW share, summing to 1 over the
        passive legs) and ``max_gap_minutes``.

    Raises:
        KeyError: if a column the pairing or a live filter needs is absent (no silent skip: a
            missing pairing column would leave every leg unpaired and the share silently at 1.0,
            i.e. exactly today's flat education relabel dressed up as a measurement).
    """
    from braunschweig.popsim import trips
    from braunschweig.popsim.escort_pairing import (
        DEFAULT_MAX_GAP_MINUTES, PASSIVE_W_ZWECK, REQUIRED_COLUMNS, STATUS_PAIRED,
        pair_passive_legs,
    )

    gap = DEFAULT_MAX_GAP_MINUTES if max_gap_minutes is None else float(max_gap_minutes)
    needed = REQUIRED_COLUMNS + ("W_GEW",)
    if exclude_rbw_legs:
        needed += ("W_RBW",)
    if drop_leading_arrive_home_leg:
        needed += ("W_SO1",)
    missing = [column for column in needed if column not in df.columns]
    if missing:
        raise KeyError(
            f"[derive_escort_w_zweck_split] the passive-escort pairing needs column(s) {missing}, "
            f"absent from the Wege frame (has {list(df.columns)}).")

    n_passive_raw = int((df["W_ZWECK"] == PASSIVE_W_ZWECK).sum())
    # The SAME reduction the trip build applies before map_purpose pairs (ruling C-R12 / #372
    # fix round 1 IMPORTANT 3): applied to the whole frame, so a dropped leg also leaves the
    # ADULT candidate pool.
    legs = trips.legs_kept_by_the_trip_build(
        df, exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=drop_leading_arrive_home_leg)

    weights = pd.to_numeric(legs["W_GEW"], errors="coerce").fillna(0.0).to_numpy()
    is_passive = (legs["W_ZWECK"] == PASSIVE_W_ZWECK).to_numpy()
    passive_weight = float(weights[is_passive].sum())
    if not passive_weight > 0:
        raise ValueError(
            "[derive_escort_w_zweck_split] total passive-escort (W_ZWECK "
            f"{PASSIVE_W_ZWECK}) weight is zero; the education share is undefined.")

    paired_frame, diagnostics = pair_passive_legs(legs, max_gap_minutes=gap)
    is_paired = (paired_frame["passive_pair_status"] == STATUS_PAIRED).to_numpy()
    realised = trips.passive_purpose_for_pairs(
        paired_frame.loc[is_paired, "passive_pair_adult_w_zweck"],
        escort_passive_education=DERIVATION_FLAGS["escort_passive_education"],
        w_zweck_10_as_leisure=DERIVATION_FLAGS["w_zweck_10_as_leisure"])

    # Purpose of EVERY passive leg: the paired ones from the adult, the unpaired ones from the
    # escort_passive_education rule -- exactly map_purpose's two branches.
    unpaired_purpose = "education" if DERIVATION_FLAGS["escort_passive_education"] else "escort"
    purpose_per_leg = np.full(len(legs), "", dtype=object)
    purpose_per_leg[is_passive] = unpaired_purpose
    purpose_per_leg[np.flatnonzero(is_paired)] = realised

    fold = {}
    for purpose in passive_fold_purposes():
        selected = is_passive & (purpose_per_leg == purpose)
        fold[purpose] = float(weights[selected].sum() / passive_weight)
    fold_total = sum(fold.values())
    if abs(fold_total - 1.0) > 1e-9:
        # Not a rounding guard but a coverage guard: a purpose missing from
        # passive_fold_purposes() would silently shrink the fold and, downstream, silently
        # move W1 mass nowhere (CLAUDE.md: no silent fallbacks).
        raise ValueError(
            f"[derive_escort_w_zweck_split] the passive purpose fold sums to {fold_total!r}, not "
            f"1.0; a purpose produced by the pairing is missing from passive_fold_purposes() "
            f"(got {sorted(set(purpose_per_leg[is_passive]))}).")

    paired_education = np.zeros(len(legs), dtype=bool)
    paired_education[np.flatnonzero(is_paired)[realised == "education"]] = True
    unpaired = is_passive & ~is_paired

    share_paired_to_active_escort = float(weights[paired_education].sum() / passive_weight)
    share_unpaired = float(weights[unpaired].sum() / passive_weight)
    stats = {
        "n_passive": int(diagnostics["n_passive"]),
        "n_passive_raw": n_passive_raw,
        "n_paired": int(diagnostics["n_paired"]),
        "share_paired": float(diagnostics["share_paired"]),
        "share_paired_to_active_escort": share_paired_to_active_escort,
        "share_unpaired": share_unpaired,
        "fold": fold,
        "max_gap_minutes": gap,
    }
    return fold["education"], stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wege", type=Path, default=DEFAULT_WEGE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    # Unit: minutes. Default None -> the model's own escort_pairing.DEFAULT_MAX_GAP_MINUTES, so
    # the committed reference is derived with exactly the window a production run pairs with.
    parser.add_argument("--max-gap-minutes", type=float, default=None,
                        help="passive/adult pairing window in MINUTES (default: the model's own)")
    args = parser.parse_args(argv)

    df = pd.read_csv(args.wege, sep=_sniff_separator(args.wege),
                     usecols=["W_ZWECK", "W_GEW", "wegkm_imp", *PAIRING_COLUMNS_NEEDED,
                              *LEG_FILTER_COLUMNS_NEEDED],
                     low_memory=False)
    df["W_ZWECK"] = pd.to_numeric(df["W_ZWECK"], errors="coerce")
    table, stats = derive_split(df)
    # The pairing runs on the WHOLE Wege frame (it needs every household member's legs, not only
    # the escort ones), so it is derived from df rather than from derive_split's escort subset.
    passive_education_share, pairing_stats = derive_passive_education_share(
        df, max_gap_minutes=args.max_gap_minutes,
        exclude_rbw_legs=DERIVATION_FLAGS["exclude_rbw_legs"],
        drop_leading_arrive_home_leg=DERIVATION_FLAGS["drop_leading_arrive_home_leg"])
    # Only the code_13 row carries the fold: these are shares WITHIN the passive legs, so writing
    # them on code_6 or on "both" would invite them being read as shares of all escort legs. The
    # education member keeps its original column name (PASSIVE_EDUCATION_SHARE_COLUMN).
    for purpose, share in pairing_stats["fold"].items():
        column = (PASSIVE_EDUCATION_SHARE_COLUMN if purpose == "education"
                  else PASSIVE_FOLD_COLUMN_TEMPLATE.format(purpose=purpose))
        table[column] = ["", round(share, 4), ""]

    if stats["weight_coercion_failures"] > 0:
        print(f"WARNING [derive_escort_w_zweck_split] W_GEW coercion failures: "
              f"{stats['weight_coercion_failures']} leg(s) with non-numeric weight "
              f"-> fallback to 0.0", flush=True)

    header = (
        "# Source: MiD 2023 Wege (local raw MiD2023_Wege.csv), W_ZWECK in {6, 13},\n"
        "# W_GEW-weighted. Code 6 = active Bringen/Holen; code 13 = the escorted\n"
        "# person's own (passive) leg (issue #256; 100% minors, verified 2026-08-11).\n"
        f"# share_weighted over ALL escort legs (n={stats['n_escort_legs']}); length stats\n"
        f"# (wegkm_imp >= 0, < 1000 km) cover {stats['length_coverage_weighted']:.4f} of the escort weight.\n"
        f"# weight_coercion_failures={stats['weight_coercion_failures']}.\n"
        "# Band columns follow mid2023_W12_triplength_by_purpose.csv (row-%).\n"
        "# code_13_to_<purpose>_share_under_pairing (code_13 row only; issue #372, ADR-0112):\n"
        "# the W_GEW purpose FOLD of the passive (code 13) legs once escort_passive_from_adult\n"
        "# pairs each with the accompanying adult's leg and gives it that adult's purpose; the\n"
        "# education member keeps the name code_13_to_education_share_under_pairing. The columns\n"
        "# sum to 1 over the passive legs. The education member decomposes into\n"
        f"# {pairing_stats['share_paired_to_active_escort']:.4f} paired with an ACTIVE escort leg\n"
        f"# + {pairing_stats['share_unpaired']:.4f} left unpaired (kept on the "
        "escort_passive_education rule).\n"
        f"# Pairing rate: {pairing_stats['n_paired']}/{pairing_stats['n_passive']} legs "
        f"({pairing_stats['share_paired']:.4f}) within "
        f"{pairing_stats['max_gap_minutes']:.0f} min.\n"
        f"# Universe: the {pairing_stats['n_passive']} passive legs the TRIP BUILD keeps, out of "
        f"{pairing_stats['n_passive_raw']} in the\n"
        "# raw table; the adult candidate pool is filtered the same way, so this describes the\n"
        "# production leg universe, not the raw one.\n"
        "# The two decomposition figures above are rounded independently of the row value, so\n"
        "# their sum can differ from it by one unit in the last place.\n"
        "# ASSUMED flag values (the INTENDED production state; escort_passive_from_adult: true is\n"
        "# added to configs/base_bs.yml by Task 7 of issue #372 and is not there yet, so a run\n"
        "# made before that task does NOT match these numbers -- nor does any run configured\n"
        "# differently):\n"
        + "".join(f"#   {key} = {value}\n" for key, value in DERIVATION_FLAGS.items())
        + f"#   escort_passive_pair_max_gap_minutes = {pairing_stats['max_gap_minutes']:.0f}\n"
        "# Generated by scripts/derive_escort_w_zweck_split.py; regenerate there, never edit.\n"
    )
    with open(args.output, "w", encoding="utf-8", newline="") as handle:
        handle.write(header)
        table.to_csv(handle, index=False)
    print(f"[derive_escort_w_zweck_split] -> {args.output}")
    print(table.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
