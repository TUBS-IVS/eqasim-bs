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
#: Name of the derived column added to the committed table (issue #372, ADR-0112).
PASSIVE_EDUCATION_SHARE_COLUMN = "code_13_to_education_share_under_pairing"
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


def derive_passive_education_share(df: pd.DataFrame, *, max_gap_minutes: float | None = None
                                   ) -> tuple[float, dict]:
    """W_GEW share of the passive escort legs that stay EDUCATION under the pairing (#372).

    With ``escort_passive_from_adult`` on, a W_ZWECK-13 leg is realised as ``education`` by
    ``trips.map_purpose`` in exactly two cases: it is PAIRED with an adult leg that is itself an
    ACTIVE escort leg (``trips.ADULT_ESCORT_W_ZWECK``, the child really is being brought to their
    own Kita/school), or it stays UNPAIRED and therefore keeps the ``escort_passive_education``
    relabel. Every other paired leg follows the adult onto shop / home / leisure / other.

    The share is computed by running the REAL pairing
    (``escort_pairing.pair_passive_legs``) and the REAL purpose derivation
    (``trips.passive_purpose_for_pairs``) on the raw Wege, never by re-deriving the rule here, so
    the committed reference cannot drift away from what the model does.

    Args:
        df: raw MiD Wege with ``W_ZWECK``, ``W_GEW`` and PAIRING_COLUMNS_NEEDED. ``W_ZWECK`` must
            already be numeric (``main()`` coerces).
        max_gap_minutes: the pairing window in minutes; ``None`` uses the model's own default.

    Returns:
        ``(share, stats)``. ``share`` is the W_GEW-weighted share of code-13 legs realised as
        education, in [0, 1]; ``stats`` carries ``n_passive``, ``n_paired``, ``share_paired``,
        the two components ``share_paired_to_active_escort`` / ``share_unpaired`` (so the
        headline number can be read, not just believed) and ``max_gap_minutes``.

    Raises:
        KeyError: if a column the pairing needs is absent (no silent skip: a missing column would
            leave every leg unpaired and the share silently at 1.0, i.e. exactly today's flat
            education relabel dressed up as a measurement).
    """
    from braunschweig.popsim import trips
    from braunschweig.popsim.escort_pairing import (
        DEFAULT_MAX_GAP_MINUTES, PASSIVE_W_ZWECK, REQUIRED_COLUMNS, STATUS_PAIRED,
        pair_passive_legs,
    )

    gap = DEFAULT_MAX_GAP_MINUTES if max_gap_minutes is None else float(max_gap_minutes)
    missing = [column for column in REQUIRED_COLUMNS + ("W_GEW",) if column not in df.columns]
    if missing:
        raise KeyError(
            f"[derive_escort_w_zweck_split] the passive-escort pairing needs column(s) {missing}, "
            f"absent from the Wege frame (has {list(df.columns)}).")

    weights = pd.to_numeric(df["W_GEW"], errors="coerce").fillna(0.0).to_numpy()
    is_passive = (df["W_ZWECK"] == PASSIVE_W_ZWECK).to_numpy()
    passive_weight = float(weights[is_passive].sum())
    if not passive_weight > 0:
        raise ValueError(
            "[derive_escort_w_zweck_split] total passive-escort (W_ZWECK "
            f"{PASSIVE_W_ZWECK}) weight is zero; the education share is undefined.")

    paired_frame, diagnostics = pair_passive_legs(df, max_gap_minutes=gap)
    is_paired = (paired_frame["passive_pair_status"] == STATUS_PAIRED).to_numpy()
    realised = trips.passive_purpose_for_pairs(
        paired_frame.loc[is_paired, "passive_pair_adult_w_zweck"],
        escort_passive_education=True, w_zweck_10_as_leisure=True)
    paired_education = np.zeros(len(df), dtype=bool)
    paired_education[np.flatnonzero(is_paired)[realised == "education"]] = True
    unpaired = is_passive & ~is_paired

    share_paired_to_active_escort = float(weights[paired_education].sum() / passive_weight)
    share_unpaired = float(weights[unpaired].sum() / passive_weight)
    stats = {
        "n_passive": int(diagnostics["n_passive"]),
        "n_paired": int(diagnostics["n_paired"]),
        "share_paired": float(diagnostics["share_paired"]),
        "share_paired_to_active_escort": share_paired_to_active_escort,
        "share_unpaired": share_unpaired,
        "max_gap_minutes": gap,
    }
    return share_paired_to_active_escort + share_unpaired, stats


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
                     usecols=["W_ZWECK", "W_GEW", "wegkm_imp", *PAIRING_COLUMNS_NEEDED],
                     low_memory=False)
    df["W_ZWECK"] = pd.to_numeric(df["W_ZWECK"], errors="coerce")
    table, stats = derive_split(df)
    # The pairing runs on the WHOLE Wege frame (it needs every household member's legs, not only
    # the escort ones), so it is derived from df rather than from derive_split's escort subset.
    passive_education_share, pairing_stats = derive_passive_education_share(
        df, max_gap_minutes=args.max_gap_minutes)
    # Only the code_13 row carries the share: it is a share WITHIN the passive legs, so writing
    # it on code_6 or on "both" would invite it being read as a share of all escort legs.
    table[PASSIVE_EDUCATION_SHARE_COLUMN] = [
        "", round(passive_education_share, 4), "",
    ]

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
        f"# {PASSIVE_EDUCATION_SHARE_COLUMN} (code_13 row only; issue #372, ADR-0112): W_GEW share\n"
        "# of the passive (code 13) legs still realised as education once escort_passive_from_adult\n"
        "# pairs each with the accompanying adult's leg. Components: paired with an ACTIVE escort leg\n"
        f"# {pairing_stats['share_paired_to_active_escort']:.4f} + left unpaired and therefore kept\n"
        f"# on the escort_passive_education rule {pairing_stats['share_unpaired']:.4f}. Pairing rate:\n"
        f"# {pairing_stats['n_paired']}/{pairing_stats['n_passive']} legs "
        f"({pairing_stats['share_paired']:.4f}) within "
        f"{pairing_stats['max_gap_minutes']:.0f} min.\n"
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
