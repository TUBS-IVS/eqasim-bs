"""Distribution-based and class-midpoint household_income_eur derivation.

Two mutually-exclusive paths that both produce the continuous
``household_income_eur`` attribute (selected by ``income_eur_from_distribution``):

- :func:`_apply_distribution_income` -- the ON path: draw a monthly net
  household income from the real MiD income-bracket distribution
  (``P(bracket | hh_size, economic_status, raumtyp)``), then apply the INKAR
  Kreis scale only as a fine within-region tilt.
- :func:`_apply_inkar_income_scale` -- the legacy OFF path: multiply the MiD
  H4 class-midpoint of the already-sampled ``household_income`` EUR-class by
  the INKAR Kreis scale.

:func:`_income_class_from_eur` re-derives the categorical EUR-class label from
a continuous EUR value (nearest-midpoint classifier) so the distribution path
keeps ``household_income`` consistent with ``household_income_eur``.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

import pandas as pd
import numpy as np

from .economic_status import ECONOMIC_STATUS_CATEGORIES
from .vehicle_ownership import _derive_kreis_ars5


# Hard-coded median € midpoint applied when a person's ``household_income``
# class label is absent from the MiD H4 class-midpoint table (the PRIMARY
# lookup). Kept at the legacy value so behaviour is unchanged; only the
# primary/fallback split is now counted and logged for transparency.
INCOME_MIDPOINT_FALLBACK_EUR = 2800.0

# Fallback-rate threshold above which the class-midpoint fallback is logged at
# WARNING level (fraction of persons in [0, 1]). A non-zero but rare fallback is
# expected only on malformed reference data; a large share signals a broken
# class-midpoint table or an unexpected income_class vocabulary.
INCOME_MIDPOINT_FALLBACK_WARN_RATE = 0.001


# --- Distribution-based household_income_eur (income_eur_from_distribution) ---
#
# Open-top heavy-tail mean. The MiD top bracket "over_7000" is open-ended; a
# finite EUR value is drawn as 7000 * (1 + Exponential(mean)). The mean 0.4 puts
# the bracket's expected value at ~9800 EUR/month (a heavy but bounded-in-practice
# right tail), matching the German top-income shape better than a uniform draw
# against an arbitrary cap. Documented in
# braunschweig.data.mid.income_by_size (INCOME_BRACKET_BOUNDS_EUR open-top note).
INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION = 0.4

# Plausible clamp for the open-top draw so a rare exponential outlier cannot push
# household_income_eur past the post-enrichment sanity range [100, 20000]. 18000
# leaves head-room below the 20000 hard cap after the INKAR fine tilt.
INCOME_OPEN_TOP_MAX_EUR = 18000.0

# Lower floor for the drawn monthly net household_income_eur. The lowest MiD
# bracket "under_500" has bounds (0, 500), so a plain uniform draw within it can
# yield an implausible near-zero net household income (observed: 1 EUR). A net
# household income below ~100 EUR/month does not occur in practice -- the MiD
# bracket has no true zero floor, it merely caps at 500. The lowest-bracket draw
# is therefore taken uniformly in [INCOME_MIN_EUR, 500) instead of [0, 500), and
# the final value is floored at INCOME_MIN_EUR after the INKAR Kreis tilt (a
# low-income Kreis tilt < 1 could otherwise push a 100 EUR value back below the
# post-enrichment sanity floor). Matches the lower bound of the sanity range
# [100, 20000] checked in execute().
INCOME_MIN_EUR = 100.0

# Fallback-rate threshold above which the distribution-income per-cell bracket pmf
# fallback (NDS base cell absent for a household's hh_size -> uniform-over-brackets
# within the cell) is escalated to WARNING. Every synthetic hh_size 1..5+ has an
# NDS base cell, so a non-trivial rate signals a malformed reference table.
INCOME_DISTRIBUTION_FALLBACK_WARN_RATE = 0.01


def _income_class_from_eur(eur_values, class_midpoint_eur):
    """Map continuous EUR values onto the BMDV income EUR-class labels.

    The categorical ``household_income`` label must stay consistent with the
    continuous ``household_income_eur`` when the latter is drawn from the MiD
    distribution. We bucket each EUR value to the income class whose midpoint is
    nearest, using the midpoints between consecutive class midpoints as the bin
    edges (a 1-D nearest-midpoint classifier). The classes are ordered by
    midpoint so the mapping is monotone in EUR.

    ``class_midpoint_eur`` is the MiD H4 class-midpoint table
    (label -> midpoint EUR). Returns a numpy object array of class labels aligned
    to ``eur_values``.
    """
    labels = list(class_midpoint_eur.keys())
    midpoints = np.array([class_midpoint_eur[k] for k in labels], dtype=float)
    order = np.argsort(midpoints)
    labels_sorted = [labels[i] for i in order]
    mids_sorted = midpoints[order]
    # Bin edges = midpoints between consecutive class midpoints.
    edges = (mids_sorted[:-1] + mids_sorted[1:]) / 2.0
    idx = np.searchsorted(edges, np.asarray(eur_values, dtype=float), side="right")
    labels_arr = np.asarray(labels_sorted, dtype=object)
    return labels_arr[idx]


def _apply_distribution_income(df_persons, df_inkar, df_bundesland, df_raumtyp,
                               df_regiostar, class_midpoint_eur, random_seed,
                               df_status_bundesland=None, df_status_raumtyp=None,
                               kreis=None):
    """Draw ``household_income_eur`` from the MiD net-income distribution.

    Per household, an income bracket is drawn from the EMPIRICAL per-cell pmf
    ``P(bracket | hh_size, economic_status, raumtyp)``. This pmf RECONCILES the
    two MiD conditionals on the income-bracket axis:

      * ``P(bracket | hh_size, raumtyp)`` -- income_by_size (NDS base + raumtyp
        tilt; :func:`braunschweig.data.mid.income_by_size.income_bracket_probabilities`);
      * ``P(bracket | status, raumtyp)`` -- income_by_status (same NDS base +
        raumtyp tilt;
        :func:`braunschweig.data.mid.income_by_status.income_bracket_probabilities_by_status`).

    The two conditionals are combined into one per-cell bracket pmf by
    :func:`braunschweig.data.mid.income_by_status.combine_size_status_bracket_pmf`
    (the IPF / odds-multiplication reconciliation
    ``P(b|size)*P(b|status)/P(b)``, with the overall region marginal ``P(b)`` from
    :func:`...overall_bracket_pmf`). Every household in the ``(size, status,
    raumtyp)`` cell then draws its bracket directly from that pmf -- so income is
    monotone in BOTH economic_status and household size by construction, and the
    realised income-by-status / income-by-size aggregates match the empirical MiD
    conditionals (within the combination/rounding tolerance).

    This REPLACES the former rank-alignment heuristic (which sorted the cell's
    households by economic_status rank and paired them with the size-only bracket
    draws). The rank alignment only ENFORCED monotonicity onto a size-only pmf; it
    did not use the empirical income x status data. The empirical conditioning is
    retained as the PRIMARY method; if the status conditional is unavailable for a
    cell (status table missing, or the NDS status base cell absent), the code
    FALLS BACK to the size-only pmf + the legacy rank-alignment, logged as a
    fallback (no silent fallback; CLAUDE.md).

    Within the chosen bracket a continuous EUR value is drawn uniformly in
    ``[low, high)``; the open top bracket uses ``7000 * (1 + Exponential(mean))``
    (clamped to :data:`INCOME_OPEN_TOP_MAX_EUR`).

    The INKAR Kreis scale is applied only as a FINE adjustment: each value is
    multiplied by ``scale[kreis] / mean(scale over kreise)``. The MiD
    distribution is already REGIONAL (NDS + raumtyp), so multiplying by the raw
    INKAR scale (which is national-mean-relative) would double-count the regional
    level; dividing by the mean scale makes INKAR a within-region Kreis tilt with
    a regional mean of ~1.0 (no level shift, only the Wolfsburg/Goslar spread).

    Finally ``household_income`` (EUR-class label) and ``high_income`` are
    re-derived from the drawn EUR via :func:`_income_class_from_eur` so the
    categorical label stays consistent with the continuous value.
    ``economic_status`` is left UNCHANGED.

    A dedicated RNG offset (+72831) is used so the OFF path and all other streams
    are untouched.
    """
    from braunschweig.data.mid.income_by_size import (
        INCOME_BRACKET_BOUNDS_EUR,
        INCOME_BRACKET_CATEGORIES,
        RS7_TO_RAUMTYP_KEY,
        income_bracket_probabilities,
    )
    from braunschweig.data.mid.income_by_status import (
        income_bracket_probabilities_by_status,
        overall_bracket_pmf,
        combine_size_status_bracket_pmf,
    )
    from braunschweig.data.bbsr.regiostar import ars_to_ags8

    # The empirical income x status conditioning is the PRIMARY method; it needs
    # both status tables. When they are absent (caller passed None) the function
    # falls back to the size-only pmf + the legacy rank-alignment, logged per cell.
    use_status = (df_status_bundesland is not None) and (df_status_raumtyp is not None)

    n_brackets = len(INCOME_BRACKET_CATEGORIES)
    bracket_low = np.array(
        [INCOME_BRACKET_BOUNDS_EUR[b][0] for b in INCOME_BRACKET_CATEGORIES], dtype=float
    )
    # Closed-bracket high; open top marked with NaN so the EUR draw branches.
    bracket_high = np.array(
        [
            np.nan if INCOME_BRACKET_BOUNDS_EUR[b][1] is None
            else INCOME_BRACKET_BOUNDS_EUR[b][1]
            for b in INCOME_BRACKET_CATEGORIES
        ],
        dtype=float,
    )

    status_rank = {s: i for i, s in enumerate(ECONOMIC_STATUS_CATEGORIES)}

    # Per-household aggregation (income is a HOUSEHOLD quantity: one draw per
    # household, broadcast to every member). household_size / economic_status are
    # household-consistent already, but we take the first per group defensively.
    has_commune = "commune_id" in df_persons.columns
    rs7_by_ags8 = dict(zip(
        df_regiostar["commune_id"].astype(str),
        df_regiostar["regiostar7"].astype("Int64"),
    ))

    work = pd.DataFrame({
        "household_id": df_persons["household_id"].to_numpy(),
        "household_size": df_persons["household_size"].astype(str).to_numpy(),
        "economic_status": df_persons["economic_status"].astype(str).to_numpy(),
    })
    if has_commune:
        work["commune_id"] = df_persons["commune_id"].astype(str).to_numpy()

    hh = work.groupby("household_id", sort=False).first()
    hh_ids = hh.index.to_numpy()
    hh_size = hh["household_size"].to_numpy()
    hh_status = hh["economic_status"].to_numpy()

    # Per-household raumtyp key (commune_id -> AGS-8 -> RS7 -> raumtyp key).
    if has_commune:
        hh_ags8 = pd.Series(hh["commune_id"].to_numpy()).map(ars_to_ags8)
        hh_rs7 = hh_ags8.map(rs7_by_ags8)
        hh_raumtyp = hh_rs7.map(
            lambda c: RS7_TO_RAUMTYP_KEY.get(int(c)) if pd.notna(c) else None
        ).to_numpy()
    else:
        hh_raumtyp = np.array([None] * len(hh_ids), dtype=object)

    n_hh = len(hh_ids)
    rng = np.random.RandomState(random_seed + 72831)

    # Per-(size, raumtyp) size-only pmf cache (the rank-alignment fallback base
    # and one input of the empirical combination).
    size_pmf_cache: dict[tuple[str, object], np.ndarray | None] = {}

    def _size_pmf_for(size_key, raumtyp_key):
        ck = (size_key, raumtyp_key)
        if ck not in size_pmf_cache:
            size_pmf_cache[ck] = income_bracket_probabilities(
                df_bundesland, df_raumtyp, size_key, raumtyp_key
            )
        return size_pmf_cache[ck]

    # Per-(size, status, raumtyp) combined pmf cache (PRIMARY empirical method):
    # combine the size conditional and the status conditional via the overall
    # region marginal. Returns None when the status conditional is unavailable for
    # that (status, raumtyp) cell (caller then rank-aligns the size-only pmf).
    status_pmf_cache: dict[tuple[str, object], np.ndarray | None] = {}
    overall_pmf_cache: dict[object, np.ndarray | None] = {}
    combined_pmf_cache: dict[tuple[str, str, object], np.ndarray | None] = {}

    def _combined_pmf_for(size_key, status_key, raumtyp_key):
        if not use_status:
            return None
        ck = (size_key, status_key, raumtyp_key)
        if ck in combined_pmf_cache:
            return combined_pmf_cache[ck]
        size_pmf = _size_pmf_for(size_key, raumtyp_key)
        if size_pmf is None:
            combined_pmf_cache[ck] = None
            return None
        sk = (status_key, raumtyp_key)
        if sk not in status_pmf_cache:
            status_pmf_cache[sk] = income_bracket_probabilities_by_status(
                df_status_bundesland, df_status_raumtyp, status_key, raumtyp_key
            )
        status_pmf = status_pmf_cache[sk]
        if raumtyp_key not in overall_pmf_cache:
            overall_pmf_cache[raumtyp_key] = overall_bracket_pmf(
                df_status_bundesland, df_status_raumtyp, raumtyp_key
            )
        overall = overall_pmf_cache[raumtyp_key]
        if status_pmf is None or overall is None:
            combined_pmf_cache[ck] = None
            return None
        combined = combine_size_status_bracket_pmf(size_pmf, status_pmf, overall)
        combined_pmf_cache[ck] = combined
        return combined

    hh_bracket = np.full(n_hh, -1, dtype=np.int64)
    n_primary = 0
    n_fallback = 0

    # Seeded per-household jitter to break economic_status ties deterministically
    # (only consumed by the rank-alignment fallback, but drawn for ALL households
    # up front so the RNG stream stays independent of how many cells fall back).
    jitter = rng.random_sample(n_hh)
    status_rank_arr = np.array(
        [status_rank.get(s, -1) for s in hh_status], dtype=float
    )

    # Group households by (hh_size, raumtyp) cell. The cell's RNG draw is one
    # uniform per household (a single rng.random_sample(m) call per cell, in the
    # stable cell-iteration order), so the stream consumption is independent of
    # whether the cell uses the PRIMARY empirical pmf or the rank-alignment
    # fallback -- only the bracket each uniform maps to differs.
    cell_df = pd.DataFrame({
        "row": np.arange(n_hh),
        "size": hh_size,
        "status": hh_status,
        "raumtyp": hh_raumtyp,
        "rank": status_rank_arr,
        "jitter": jitter,
    })
    for (size_key, raumtyp_key), grp in cell_df.groupby(["size", "raumtyp"], dropna=False, sort=False):
        rows = grp["row"].to_numpy()
        m = len(rows)
        rk = raumtyp_key if raumtyp_key is not None else None
        # One uniform per household for this cell (fixed RNG consumption).
        u = rng.random_sample(m)

        # PRIMARY: draw each household's bracket directly from the empirical
        # P(bracket | size, status, raumtyp) (monotone in BOTH dimensions by
        # construction). The combined pmf depends on the household's status, so we
        # group the cell by status and draw within each status sub-group using the
        # cell's pre-drawn uniforms (sliced in stable row order so the consumption
        # is unchanged). If the combined pmf is unavailable for ALL statuses in the
        # cell (status table / cell missing), we fall back to the size-only pmf +
        # rank alignment for the whole cell.
        drawn = np.full(m, -1, dtype=np.int64)
        status_arr_cell = grp["status"].to_numpy()
        any_primary = False
        for si in range(m):
            combined = _combined_pmf_for(size_key, status_arr_cell[si], rk)
            if combined is None:
                continue
            cdf = np.cumsum(combined)
            b = int(np.searchsorted(cdf, u[si], side="right"))
            drawn[si] = min(max(b, 0), n_brackets - 1)
            any_primary = True

        if any_primary and (drawn >= 0).all():
            # Full empirical coverage for this cell: every household drew from its
            # (size, status, raumtyp) pmf. Income is monotone in status because the
            # per-status pmf is monotone (combination preserves the status order).
            hh_bracket[rows] = drawn
            n_primary += m
            continue

        # FALLBACK: size-only pmf + legacy rank-alignment for the whole cell. Used
        # when the empirical income x status conditioning is unavailable (status
        # table absent, or NDS status cell missing for some/all households here).
        size_pmf = _size_pmf_for(size_key, rk)
        if size_pmf is None:
            # NDS base cell absent for this hh_size -> uniform over brackets.
            size_pmf = np.full(n_brackets, 1.0 / n_brackets)
        cdf = np.cumsum(size_pmf)
        drawn_fb = np.searchsorted(cdf, u, side="right")
        drawn_fb = np.clip(drawn_fb, 0, n_brackets - 1)
        # Rank-align: sort households by (status rank, jitter) ascending and the
        # drawn brackets ascending, then pair them so higher-status households get
        # higher brackets. Keeps the realised bracket marginal EXACTLY the
        # multinomial draw while making the bracket monotone in status.
        rank_key = grp["rank"].to_numpy() + grp["jitter"].to_numpy() * 1e-6
        hh_order = np.argsort(rank_key, kind="stable")
        bracket_sorted = np.sort(drawn_fb, kind="stable")
        hh_bracket[rows[hh_order]] = bracket_sorted
        n_fallback += m

    # Draw a continuous EUR value within each household's bracket.
    hh_low = bracket_low[hh_bracket]
    hh_high = bracket_high[hh_bracket]
    eur = np.empty(n_hh, dtype=float)
    is_open_top = np.isnan(hh_high)
    closed = ~is_open_top
    if closed.any():
        u_eur = rng.random_sample(int(closed.sum()))
        # Floor the lowest bracket's draw low at INCOME_MIN_EUR so the open-bottom
        # "under_500" bracket (low=0) cannot yield an implausible near-zero income;
        # all higher brackets have low >= 500 > INCOME_MIN_EUR (no-op for them).
        low_draw = np.maximum(hh_low[closed], INCOME_MIN_EUR)
        eur[closed] = low_draw + u_eur * (hh_high[closed] - low_draw)
    if is_open_top.any():
        n_top = int(is_open_top.sum())
        exp_draw = rng.exponential(
            scale=INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION, size=n_top
        )
        top_vals = hh_low[is_open_top] * (1.0 + exp_draw)
        eur[is_open_top] = np.minimum(top_vals, INCOME_OPEN_TOP_MAX_EUR)

    # INKAR FINE Kreis tilt (scale / mean(scale)), broadcast per household.
    if kreis is None:
        kreis = _derive_kreis_ars5(df_persons)
    kreis_by_hh = (
        pd.Series(kreis.to_numpy(), index=df_persons["household_id"])
        .groupby(level=0).first()
    )
    hh_kreis = pd.Series(hh_ids).map(kreis_by_hh).to_numpy()
    scale_lookup = dict(zip(df_inkar["ars5"], df_inkar["scale"]))
    raw_scale = pd.Series(hh_kreis).map(scale_lookup).astype(float)
    # Join-coverage transparency (CLAUDE.md no-silent-fallback): a household
    # Kreis absent from the INKAR table falls back to fine_tilt=1.0 below. A
    # format drift of the INKAR ars5 keys would silently no-op the WHOLE
    # spatial income tilt (mean scale 1.0 looks legitimate), so count it.
    _n_scale_miss = int(raw_scale.isna().sum())
    if _n_scale_miss:
        _miss_rate = _n_scale_miss / len(raw_scale) if len(raw_scale) else 0.0
        print(
            f"[braunschweig.enriched] {'WARNING: ' if _miss_rate > 0.05 else ''}"
            f"INKAR fine income tilt: {_n_scale_miss}/{len(raw_scale)} households "
            f"({100.0 * _miss_rate:.1f}%) have no INKAR scale for their Kreis -> "
            f"tilt 1.0. A high rate means an ars5 key mismatch "
            f"(INKAR keys: {sorted(map(str, scale_lookup))[:5]})."
        )
    # Mean over the IN-SCOPE kreise present in the population (not the national
    # INKAR mean) so the fine tilt has a population mean of ~1.0.
    in_scope_scales = [
        scale_lookup[k] for k in pd.unique(hh_kreis)
        if k in scale_lookup
    ]
    mean_scale = float(np.mean(in_scope_scales)) if in_scope_scales else 1.0
    fine_tilt = (raw_scale / mean_scale).fillna(1.0).to_numpy()
    eur = eur * fine_tilt

    # Defense-in-depth floor: a low-income Kreis tilt (< 1) could push a draw at the
    # bracket floor below INCOME_MIN_EUR; clamp so the value never breaches the
    # post-enrichment sanity floor [100, 20000] (the open-top is already capped).
    eur = np.maximum(eur, INCOME_MIN_EUR)

    # Broadcast the household EUR back to every person.
    eur_by_hh = dict(zip(hh_ids, np.round(eur, 0)))
    df_persons["household_income_eur"] = (
        df_persons["household_id"].map(eur_by_hh).astype(float).to_numpy()
    )

    # Re-derive the EUR-class label + high_income from the continuous value so the
    # categorical household_income stays consistent (economic_status untouched).
    new_class = _income_class_from_eur(
        df_persons["household_income_eur"].to_numpy(), class_midpoint_eur
    )
    df_persons["household_income"] = new_class
    df_persons["high_income"] = df_persons["household_income"] == "5000+"

    fallback_rate = (n_fallback / n_hh) if n_hh else 0.0
    df_persons.attrs["income_distribution_primary_count"] = n_primary
    df_persons.attrs["income_distribution_fallback_count"] = n_fallback
    df_persons.attrs["income_distribution_fallback_rate"] = fallback_rate
    df_persons.attrs["income_distribution_use_status"] = bool(use_status)
    level = "WARNING: " if fallback_rate > INCOME_DISTRIBUTION_FALLBACK_WARN_RATE else ""
    primary_label = (
        "empirical P(bracket|size,status,raumtyp)" if use_status
        else "size-only P(bracket|size,raumtyp) + rank-alignment (status tables absent)"
    )
    print(
        f"[braunschweig.enriched] {level}distribution household_income_eur: "
        f"{primary_label} primary {n_primary}/{n_hh} households "
        f"({1 - fallback_rate:.2%}), fallback (size-only pmf + rank-alignment; "
        f"status cell / NDS base absent) {n_fallback} ({fallback_rate:.2%}). "
        f"INKAR applied as a fine Kreis tilt (mean scale {mean_scale:.3f}). "
        f"mean income {np.mean(eur):.0f} EUR."
    )
    print(
        "[braunschweig.enriched] mean household_income_eur by economic_status = "
        + ", ".join(
            f"{s}={df_persons.loc[df_persons['economic_status'] == s, 'household_income_eur'].mean():.0f}"
            for s in ECONOMIC_STATUS_CATEGORIES
        )
    )
    return df_persons


def _apply_inkar_income_scale(df_persons, df_inkar, class_midpoint_eur,
                              kreis=None):
    """Add ``household_income_eur`` = class_midpoint * INKAR-scale[home_kreis].

    ``kreis`` is the per-person AGS-5 Series from :func:`_derive_kreis_ars5`,
    accepted so the caller can reuse a single derivation (passing ``None``
    derives it locally; output-identical).

    Fallback transparency: each person's € midpoint comes either from the
    PRIMARY per-class lookup in ``class_midpoint_eur`` (the MiD H4 class-midpoint
    table) or, when the person's ``household_income`` class label is absent from
    that table, from the hard-coded FALLBACK median midpoint
    (:data:`INCOME_MIDPOINT_FALLBACK_EUR`). The primary/fallback split is counted
    and the fallback rate is logged; a rate above
    :data:`INCOME_MIDPOINT_FALLBACK_WARN_RATE` is escalated to a WARNING and the
    distinct unmapped class labels are listed. The counts are also stored on
    ``df_persons.attrs`` so callers/tests can assert the primary mapping was
    taken without a signature change. This logging is purely observational: it
    does not alter any computed ``household_income_eur`` value.
    """
    midpoint = df_persons["household_income"].astype(str).map(class_midpoint_eur)
    n_total = int(len(midpoint))
    fallback_mask = midpoint.isna()
    n_fallback = int(fallback_mask.sum())
    n_primary = n_total - n_fallback
    fallback_rate = (n_fallback / n_total) if n_total else 0.0
    df_persons.attrs["income_midpoint_primary_count"] = n_primary
    df_persons.attrs["income_midpoint_fallback_count"] = n_fallback
    df_persons.attrs["income_midpoint_fallback_rate"] = fallback_rate
    if n_fallback:
        unknown_classes = sorted(
            df_persons.loc[fallback_mask.values, "household_income"]
            .astype(str).unique().tolist()
        )
        level = (
            "WARNING: "
            if fallback_rate > INCOME_MIDPOINT_FALLBACK_WARN_RATE
            else ""
        )
        print(
            f"[braunschweig.enriched] {level}income class-midpoint fallback used "
            f"for {n_fallback}/{n_total} persons "
            f"({fallback_rate:.2%}); primary lookup hit {n_primary}. "
            f"Unmapped income_class labels {unknown_classes}; "
            f"using median midpoint {INCOME_MIDPOINT_FALLBACK_EUR:.0f} EUR."
        )
        midpoint = midpoint.fillna(INCOME_MIDPOINT_FALLBACK_EUR)
    else:
        print(
            f"[braunschweig.enriched] income class-midpoint PRIMARY lookup hit "
            f"all {n_primary}/{n_total} persons (fallback rate 0.00%)."
        )

    if kreis is None:
        kreis = _derive_kreis_ars5(df_persons)
    scale_lookup = dict(zip(df_inkar["ars5"], df_inkar["scale"]))
    scale_raw = kreis.map(scale_lookup)
    # Join-coverage transparency (CLAUDE.md no-silent-fallback): misses fall
    # back to scale 1.0; a wholesale ars5 mismatch would silently disable the
    # entire Kreis income scaling, so the miss rate must be visible.
    n_scale_miss = int(scale_raw.isna().sum())
    if n_scale_miss:
        miss_rate = n_scale_miss / len(scale_raw) if len(scale_raw) else 0.0
        print(
            f"[braunschweig.enriched] {'WARNING: ' if miss_rate > 0.05 else ''}"
            f"INKAR income scale: {n_scale_miss}/{len(scale_raw)} persons "
            f"({100.0 * miss_rate:.1f}%) have no INKAR scale for their Kreis -> "
            f"scale 1.0. A high rate means an ars5 key mismatch "
            f"(INKAR keys: {sorted(map(str, scale_lookup))[:5]})."
        )
    scale = scale_raw.fillna(1.0).astype(float)

    df_persons["household_income_eur"] = (midpoint.astype(float) * scale).round(0)
    return df_persons
