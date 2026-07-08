"""Per-Kreis target blending: combine a MiD regional table, an SrV 2023 table
and an optional full-count register arbiter into ONE target frame per
attribute, per the 2026-07-08 control-sourcing rules.

Source hierarchy applied per Kreis: two-survey agreement -> precision blend;
disagreement + arbiter -> the survey whose Kreis RANK (on a scalar summary of
the category shares) is closer to the register rank; disagreement without
arbiter -> MiD shrunk toward the MiD region aggregate; Kreise without SrV
coverage (Wolfsburg) and the Gesamt row -> MiD unchanged. Pure module: no
file IO, fully unit-testable. The emitted frames are FINAL targets — the
downstream kreis_attribute_control shrinkage (prior_n) must stay 0 for them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BlendConfig:
    # Max per-category difference (percentage points) treated as agreement.
    tolerance_pp: float = 5.0
    # Pull toward the MiD Gesamt row when surveys disagree and no arbiter exists.
    disagreement_shrink_lambda: float = 0.3


def _renorm(row: pd.Series, categories: list) -> pd.Series:
    total = row[categories].sum()
    if total <= 0:
        raise ValueError(f"target row sums to {total}: {row.to_dict()}")
    row[categories] = row[categories] / total
    return row


def _ranks(scores: pd.Series) -> pd.Series:
    # rank 1 = highest score; average ranks on ties keep distances symmetric.
    return scores.rank(ascending=False, method="average")


def blend_kreis_target(mid_df: pd.DataFrame, srv_df: pd.DataFrame,
                       categories: list, *, arbiter: pd.Series | None = None,
                       rank_score_columns: list | None = None,
                       config: BlendConfig = BlendConfig()) -> pd.DataFrame:
    if arbiter is not None and not rank_score_columns:
        raise ValueError("rank_score_columns is required when an arbiter is given.")
    mid = mid_df.set_index("ars5")
    srv = srv_df.set_index("code")
    covered = [k for k in mid.index if k != "Gesamt" and k in srv.index]

    rank_dist_mid = rank_dist_srv = None
    if arbiter is not None:
        score = lambda frame: frame.loc[covered, rank_score_columns].sum(axis=1)
        arb_ranks = _ranks(arbiter.loc[covered])
        rank_dist_mid = (_ranks(score(mid)) - arb_ranks).abs()
        rank_dist_srv = (_ranks(score(srv)) - arb_ranks).abs()

    out = []
    for ars5, mrow in mid.iterrows():
        rec = {"ars5": ars5, **{c: float(mrow[c]) for c in categories}}
        if ars5 == "Gesamt" or ars5 not in srv.index:
            rec.update(source="mid",
                       n_effective=int(mrow.get("n_unweighted", 0) or 0)
                       if ars5 != "Gesamt" else 0)
            out.append(rec)
            continue
        srow = srv.loc[ars5]
        n_srv = int(srow["n_unweighted"])
        if "n_unweighted" in mid.columns and pd.notna(mrow.get("n_unweighted")):
            n_mid = int(mrow["n_unweighted"])
        else:
            # ASSUMPTION: MiD table carries no n -> equal precision weights.
            n_mid = n_srv
            log.warning("blend[%s]: MiD n_unweighted missing, assuming "
                        "n_mid = n_srv = %d", ars5, n_srv)
        max_diff_pp = max(abs(float(mrow[c]) - float(srow[c])) * 100.0
                          for c in categories)

        def apply_blend():
            for c in categories:
                rec[c] = (n_mid * float(mrow[c]) + n_srv * float(srow[c])) / (n_mid + n_srv)
            rec.update(source="blend", n_effective=n_mid + n_srv)

        if max_diff_pp <= config.tolerance_pp:
            apply_blend()
        elif arbiter is not None:
            d_mid, d_srv = float(rank_dist_mid[ars5]), float(rank_dist_srv[ars5])
            if d_mid < d_srv:
                rec.update(source="mid_arbitrated", n_effective=n_mid)
            elif d_srv < d_mid:
                for c in categories:
                    rec[c] = float(srow[c])
                rec.update(source="srv_arbitrated", n_effective=n_srv)
            else:
                apply_blend()
        else:
            lam = config.disagreement_shrink_lambda
            gesamt = mid.loc["Gesamt"]
            for c in categories:
                rec[c] = (1.0 - lam) * float(mrow[c]) + lam * float(gesamt[c])
            rec.update(source="mid_shrunk", n_effective=n_mid)
        out.append(rec)

    frame = pd.DataFrame(out).apply(_renorm, axis=1, categories=categories)
    counts = frame["source"].value_counts().to_dict()
    log.info("blend sources: %s", counts)  # fallback-transparency: which rule fired
    return frame[["ars5", "source", "n_effective", *categories]]
