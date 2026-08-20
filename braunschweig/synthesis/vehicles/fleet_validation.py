"""Post-synthesis validator: realised fleet marginals vs the EFFECTIVE fed-in targets.

The fleet is drawn from targets that are deliberately transformed (per-Kreis
rake, income-age tilt, euro-age consistency projection). This compares the
realised marginals to those EFFECTIVE targets (NOT the raw KBA tables) so the
residual collapses to Monte-Carlo sampling error on a healthy model and still
catches implementation bugs (e.g. the 4-yr age offset). Logging-only; no
silent drift. See docs/superpowers/specs/2026-07-01-fleet-model-improvements-design.md.

F4 NOTE (combustion-split traceability): the per-Kreis electric rake re-centres
only the ELECTRIC (bev/phev) mass to the per-Kreis target. After the per-model
fuel-weight mask, the per-Kreis petrol:diesel:gas:hybrid split is therefore NOT
guaranteed to match the 46251-02 / FZ 27.15 combustion marginal, and this
validator compares against the (already-weighted) EFFECTIVE pmf, so it does not
police that drift. The realised per-Kreis combustion split should be
spot-checked against 46251-02 in the per-run validation summary.
"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd

LOGGER = logging.getLogger("braunschweig.synthesis.vehicles.fleet_validation")

#: Powertrains counted as "electric" for the RegioStaR7 KBA cross-check below.
#: Matches the KBA "Pkw Elektro Anteil" definition used throughout this project's
#: KBA extractors (see scripts/extract_kba_fleet.py::extract_gemeinde_ev's
#: ev_share, which likewise combines BEV + PHEV + fuel-cell into one "Elektro"
#: share, distinct from the separately reported bev_share/phev_share columns).
_ELECTRIC_LIKE_POWERTRAINS = ("bev", "phev", "hydrogen")


def _shares(series):
    vc = series.value_counts(dropna=False)
    n = float(vc.sum())
    return {str(k): float(v) / n for k, v in vc.items()}, int(n)


def validate_realised_margins(df_spec, expected, sample_rate: float = 1.0,
                              tol_sigma: float = 4.0) -> dict:
    """Compare realised marginals in df_spec to the effective target PMFs.

    A dimension is flagged when the max absolute per-label deviation exceeds a
    Monte-Carlo band: tol_sigma * sqrt(p*(1-p)/N_eff) (in pp), N_eff scaled by
    sample_rate so a 1% sample is not falsely flagged. tol_sigma default 4.
    """
    out = {"dimensions": {}, "any_flagged": False}
    for dim, exp in expected.items():
        if dim not in df_spec.columns:
            continue
        realised, n = _shares(df_spec[dim])
        n_eff = max(1.0, n * float(sample_rate))
        labels = set(realised) | set(exp)
        max_pp = 0.0
        band_pp = 0.0
        for lab in labels:
            r = realised.get(lab, 0.0); e = float(exp.get(lab, 0.0))
            max_pp = max(max_pp, abs(r - e) * 100.0)
            band_pp = max(band_pp, tol_sigma * np.sqrt(max(e * (1 - e), 1e-9) / n_eff) * 100.0)
        flagged = bool(max_pp > band_pp)
        out["dimensions"][dim] = {"realised": realised, "expected": exp,
                                  "max_abs_pp": round(max_pp, 3),
                                  "band_pp": round(band_pp, 3), "flagged": flagged}
        out["any_flagged"] = out["any_flagged"] or flagged
        (LOGGER.warning if flagged else LOGGER.info)(
            "[fleet_validation] %s: max dev %.2fpp (band %.2fpp) -> %s",
            dim, max_pp, band_pp, "DRIFT" if flagged else "ok")
    return out


def crosscheck_ev_by_regiostar7(df_spec, df_rs7) -> dict:
    """LOGGING-ONLY cross-check: realised EV share per home RegioStaR-7 code vs the
    national KBA RegioStaR-7 EV share (``kba_ev_regiostar7.csv``, Task B6).

    This NEVER flags the run and NEVER raises: the KBA reference is a NATIONAL
    aggregate while the synthesised fleet is regional (Zukunftsregion
    Braunschweig only). Per the project's no-invented-reference-value rule
    (CLAUDE.md), a national figure cannot be treated as a regional target, so
    this is reported purely as an order-of-magnitude CROSS-CHECK for the run
    summary -- never as a pass/fail validation dimension (unlike
    :func:`validate_realised_margins`, this helper has no ``flagged`` output).

    Args:
        df_spec: The realised fleet spec (one row per car), expected to carry
            ``raumtyp`` (home RegioStaR-7 code 71..77, possibly ``NaN``) and
            ``powertrain`` (one of
            :data:`braunschweig.data.kba.fleet_tables.POWERTRAIN_LABELS`).
        df_rs7: The KBA RegioStaR7 reference table (columns ``rs7, ev_share,
            stichtag``), typically from
            :func:`braunschweig.data.kba.fleet_tables.load_ev_regiostar7`.

    Returns:
        Dict keyed by the RegioStaR-7 int code -> ``{"realised": float,
        "reference": float, "delta_pp": float, "n_cars": int}``, where
        ``realised`` is the observed ``bev + phev + hydrogen`` share of cars
        whose home is in that RegioStaR-7 code and ``reference`` is the KBA
        national EV share for the same code. Returns an empty dict if the
        required columns are absent or no row has a usable RegioStaR-7 code --
        this is logged, never raised.
    """
    out: dict = {}
    required_spec_columns = {"raumtyp", "powertrain"}
    required_rs7_columns = {"rs7", "ev_share"}
    if not required_spec_columns.issubset(df_spec.columns):
        LOGGER.info(
            "[crosscheck_ev_by_regiostar7] df_spec missing %s -- cross-check "
            "skipped (national reference; logging-only, never flags the run).",
            sorted(required_spec_columns - set(df_spec.columns)),
        )
        return out
    if not required_rs7_columns.issubset(df_rs7.columns):
        LOGGER.info(
            "[crosscheck_ev_by_regiostar7] df_rs7 missing %s -- cross-check "
            "skipped (national reference; logging-only, never flags the run).",
            sorted(required_rs7_columns - set(df_rs7.columns)),
        )
        return out

    reference = df_rs7.set_index("rs7")["ev_share"].to_dict()
    for rs7_code, group in df_spec.groupby("raumtyp"):
        if pd.isna(rs7_code):
            continue
        rs7_int = int(rs7_code)
        reference_share = reference.get(rs7_int)
        if reference_share is None:
            LOGGER.info(
                "[crosscheck_ev_by_regiostar7] rs7=%d: no KBA reference row -- "
                "skipped.", rs7_int,
            )
            continue
        n_cars = len(group)
        realised_share = float(group["powertrain"].isin(_ELECTRIC_LIKE_POWERTRAINS).sum()) / n_cars
        delta_pp = (realised_share - float(reference_share)) * 100.0
        out[rs7_int] = {
            "realised": round(realised_share, 4),
            "reference": round(float(reference_share), 4),
            "delta_pp": round(delta_pp, 3),
            "n_cars": n_cars,
        }
        LOGGER.info(
            "[crosscheck_ev_by_regiostar7] rs7=%d: realised=%.2f%% vs KBA national "
            "reference=%.2f%% (delta=%.2fpp, n=%d cars) -- CROSS-CHECK only "
            "(national reference vs regional model), NOT a validation target.",
            rs7_int, realised_share * 100.0, float(reference_share) * 100.0,
            delta_pp, n_cars,
        )
    return out


def validate_wohnmobile_holder_age(df_spec, tilt_model, sample_rate: float = 1.0,
                                   tol_sigma: float = 4.0,
                                   composition_band_pp: float = 2.0,
                                   min_motorhomes: int = 30,
                                   expected_realised_share: float | None = None,
                                   ) -> dict:
    """Issue #315 acceptance check: holder-age composition + preserved share.

    (1) Composition: realised ``P(age class | wohnmobile)`` (over drawn
        motorhomes with a classifiable ``owner_age``) vs the KBA reference
        ``tilt_model.ref_share``. A class is flagged when it deviates by more
        than ``max(composition_band_pp, tol_sigma * sqrt(p(1-p)/N_eff))`` -- the
        MC floor keeps a small smoke (few motorhomes) from false-flagging.
        Skipped (with a stated reason) below ``min_motorhomes``.
    (2) Aggregate: the realised wohnmobile share of ALL cars (the FINAL
        ``segment`` column, which INCLUDES any car that landed on
        ``wohnmobile`` via the consistency_v2 sonstige-redraw -- see
        ``sample_fleet``) vs an expectation, flagged beyond
        ``tol_sigma * sqrt(p(1-p)/N_eff)``:

        * ``expected_realised_share`` given (the caller's per-car-averaged
          EFFECTIVE segment target -- tilt AND sonstige redistribution
          included, i.e. exactly what the draw targets): the flag compares
          realised against IT. This is the unbiased implementation check --
          realised and expected are the same quantity by construction, so only
          Monte-Carlo noise should separate them, at any N.
        * ``expected_realised_share`` is ``None`` (standalone use, e.g. a unit
          test that fits/tilts without running the full sonstige-redraw):
          falls back to flagging against ``tilt_model.expected_wm_share`` (the
          UNTILTED expectation), matching the pre-fix behaviour, so the
          function stays usable standalone. CAVEAT: the sonstige-redraw is not
          modelled in this mode, so the realised share carries a small,
          deterministic, age-independent positive offset over
          ``expected_wm_share`` (approximately the wohnmobile share of the
          modelled redistribution pmf, times the mean redistributed
          "sonstige" mass per car -- see ADR-0093). Unlike Monte-Carlo noise,
          that offset does not shrink with N, so at large N it can exceed the
          sigma band and falsely flag DRIFT; the production caller
          (:mod:`fleet_sampling_de`) always supplies
          ``expected_realised_share`` to avoid this bias.

        ``tilt_model.expected_wm_share`` is reported in the output as
        ``expected_untilted`` (with ``dev_untilted_pp``, the realised share's
        deviation from it in percentage points) whenever it is available,
        REGARDLESS of which target was used for the flag above -- this is the
        tilt-NEUTRALITY evidence (the calibration's exact-aggregate-
        preservation claim, ADR-0093, is against the untilted expectation) and
        is intentionally NEVER flagged: ``dev_untilted_pp`` carries the
        age-independent sonstige-redraw leak described above and is expected
        to be slightly positive, not a sign of a broken tilt.
    Logging + summary only (mirrors :func:`validate_realised_margins`).
    """
    out = {"n_motorhomes": 0, "composition": {}, "aggregate": {},
           "flagged": False, "skipped_reason": None}
    if "segment" not in df_spec.columns or "owner_age" not in df_spec.columns:
        out["skipped_reason"] = "df_spec lacks segment/owner_age"
        LOGGER.info("[wohnmobile_holder_age] skipped: %s.", out["skipped_reason"])
        return out
    n_total = len(df_spec)
    n_eff_total = max(1.0, n_total * float(sample_rate))
    wm = df_spec[df_spec["segment"] == "wohnmobile"]
    out["n_motorhomes"] = int(len(wm))

    expected_untilted = tilt_model.expected_wm_share
    # The unbiased target when supplied (realised and expected are the SAME
    # quantity by construction); else fall back to the untilted expectation,
    # which carries the sonstige-redraw leak described in the docstring above.
    flag_target = (expected_realised_share if expected_realised_share is not None
                  else expected_untilted)
    if flag_target is not None and n_total > 0:
        realised = float(len(wm)) / n_total
        band = tol_sigma * float(np.sqrt(
            max(flag_target * (1.0 - flag_target), 1e-12) / n_eff_total))
        agg_flag = bool(abs(realised - flag_target) > band)
        out["aggregate"] = {
            "realised": round(realised, 6),
            "dev_pp": round((realised - flag_target) * 100.0, 4),
            "band_pp": round(band * 100.0, 4),
            "flagged": agg_flag,
        }
        if expected_realised_share is not None:
            out["aggregate"]["expected_effective"] = round(
                float(expected_realised_share), 6)
        if expected_untilted is not None:
            out["aggregate"]["expected_untilted"] = round(float(expected_untilted), 6)
            out["aggregate"]["dev_untilted_pp"] = round(
                (realised - expected_untilted) * 100.0, 4)
        out["flagged"] = out["flagged"] or agg_flag
        (LOGGER.warning if agg_flag else LOGGER.info)(
            "[wohnmobile_holder_age] aggregate: realised %.4f%% vs %s "
            "%.4f%% (dev %+.3fpp, band %.3fpp) -> %s",
            realised * 100.0,
            "effective" if expected_realised_share is not None else "untilted",
            flag_target * 100.0, (realised - flag_target) * 100.0, band * 100.0,
            "DRIFT" if agg_flag else "ok")
        if expected_untilted is not None and expected_realised_share is not None:
            # Reported, never flagged: the tilt-neutrality evidence (see the
            # docstring for why a small positive dev_untilted_pp is expected).
            LOGGER.info(
                "[wohnmobile_holder_age] aggregate vs untilted (reported only, "
                "carries the sonstige-redraw leak): %.4f%% (dev_untilted "
                "%+.3fpp).", expected_untilted * 100.0,
                (realised - expected_untilted) * 100.0)

    if len(wm) < min_motorhomes:
        out["skipped_reason"] = (
            f"only {len(wm)} motorhomes < {min_motorhomes}: composition check "
            f"skipped (aggregate check above still ran)")
        LOGGER.info("[wohnmobile_holder_age] %s.", out["skipped_reason"])
        return out
    classes = wm["owner_age"].map(tilt_model.age_class_for)
    valid = classes.notna()
    n_wm_valid = int(valid.sum())
    if n_wm_valid == 0:
        out["skipped_reason"] = "no motorhome carries a classifiable owner_age"
        LOGGER.warning("[wohnmobile_holder_age] %s.", out["skipped_reason"])
        return out
    realised_comp = classes[valid].value_counts(normalize=True).to_dict()
    n_eff_wm = max(1.0, n_wm_valid * float(sample_rate))
    comp_flagged = False
    for label, ref in tilt_model.ref_share.items():
        realised_share = float(realised_comp.get(label, 0.0))
        dev_pp = abs(realised_share - ref) * 100.0
        mc_pp = tol_sigma * float(np.sqrt(
            max(ref * (1.0 - ref), 1e-12) / n_eff_wm)) * 100.0
        band_pp = max(composition_band_pp, mc_pp)
        flag = bool(dev_pp > band_pp)
        comp_flagged = comp_flagged or flag
        out["composition"][label] = {
            "realised": round(realised_share, 4), "reference": round(ref, 4),
            "dev_pp": round(dev_pp, 3), "band_pp": round(band_pp, 3),
            "flagged": flag,
        }
    out["flagged"] = out["flagged"] or comp_flagged
    (LOGGER.warning if comp_flagged else LOGGER.info)(
        "[wohnmobile_holder_age] composition over %d motorhomes: max dev %.2fpp "
        "-> %s", n_wm_valid,
        max(v["dev_pp"] for v in out["composition"].values()),
        "DRIFT" if comp_flagged else "ok")
    return out
