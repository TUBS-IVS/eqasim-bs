"""Work-OD production mass for the gravity model (#132).

The gravity work OD historically used TOTAL resident population as the
per-Gemeinde production (origin) mass -- an implicit assumption of a
spatially uniform employment rate. This module provides the flag-gated
alternative: ``svb_wohn`` (employed residents at place of residence, BA
Gemeindedaten XLSX), used consistently at every point where the production
mass enters (gravity row margin, Pendleratlas-IPF seeding, outbound flows).

ASSUMPTION (documented, spec 2026-07-15): svb_wohn counts SvB only (no
Beamte/Selbststaendige/gfB-only workers); it is used as a PROXY for the
spatial variation of the total employment rate. Kreis totals are unaffected
by construction (the Kreis-level Pendleratlas IPF is the binding anchor).

Fallback transparency (CLAUDE.md, mandatory): Gemeinden without a usable
svb_wohn value get the Kreis-mean employment rate (global mean when a whole
Kreis lacks data) applied to their population; the primary/fallback rate is
logged and a WARNING is emitted above ``warn_share``.
"""
from __future__ import annotations

import os

import pandas as pd

PRODUCTION_MASS_MODES = ("population", "svb_wohn")
SVB_FALLBACK_WARN_SHARE = 0.05


def read_svb_wohn_per_commune(context) -> pd.DataFrame:
    """Read per-Gemeinde svb_wohn from the BA Gemeindedaten XLSX.

    Mirrors ``braunschweig.gravity.model._read_betriebe_per_commune`` (same
    file, sheet, AGS normalisation and AGS->commune_id mapping via
    ``eqasim_common.spatial.codes``) but extracts the ``svb_wohn`` column.
    Returns columns ``commune_id`` (12-digit ARS), ``svb_wohn`` (int).
    """
    # Local import to avoid a circular import at module load time
    # (model.py imports this module).
    from braunschweig.gravity.model import _GEMBAND_COLUMN_NAMES

    path = os.path.join(
        context.config("data_path"),
        context.config("braunschweig.employment_gemband_path"),
    )
    if not os.path.exists(path):
        raise RuntimeError(
            "[braunschweig.gravity.production_mass] work_production_mass="
            f"svb_wohn is enabled but the BA Gemeindedaten XLSX is missing: {path}"
        )

    df = pd.read_excel(
        path, sheet_name="Gemeindedaten",
        header=None, skiprows=8, names=_GEMBAND_COLUMN_NAMES,
    )
    df = df.dropna(subset=["ags"]).copy()
    df["ags"] = df["ags"].astype(str).str.strip()

    df_codes = context.stage("eqasim_common.spatial.codes")
    scope_kreise = set(df_codes["departement_id"].astype(str).unique())

    mask_krfr = (df["ags"].str.len() == 5) & df["ags"].isin(scope_kreise)
    df.loc[mask_krfr, "ags"] = df.loc[mask_krfr, "ags"] + "000"
    df = df[(df["ags"].str.len() == 8) & df["ags"].str[:5].isin(scope_kreise)].copy()

    df["svb_wohn"] = (
        pd.to_numeric(df["svb_wohn"], errors="coerce").fillna(0).astype(int)
    )
    df_codes_scope = df_codes[df_codes["departement_id"].astype(str).isin(scope_kreise)]
    df = df.merge(
        df_codes_scope[["ags", "commune_id"]].astype(str), on="ags", how="inner",
    )
    return df[["commune_id", "svb_wohn"]]


def build_work_production_mass(df_population: pd.DataFrame,
                               df_svb: pd.DataFrame,
                               mode: str,
                               warn_share: float = SVB_FALLBACK_WARN_SHARE) -> pd.DataFrame:
    """Return the work production frame [origin_id, population] for *mode*."""
    if mode not in PRODUCTION_MASS_MODES:
        raise ValueError(
            f"unknown braunschweig.gravity.work_production_mass {mode!r}; "
            f"expected one of {PRODUCTION_MASS_MODES}"
        )
    if mode == "population":
        return df_population

    df = df_population.copy()
    df["origin_id"] = df["origin_id"].astype(str)
    svb = df_svb.copy()
    svb["commune_id"] = svb["commune_id"].astype(str)
    df = df.merge(svb.rename(columns={"commune_id": "origin_id"}),
                  on="origin_id", how="left")

    usable = df["svb_wohn"].notna() & (df["svb_wohn"] > 0)
    df["kreis"] = df["origin_id"].str[:5]
    with_svb = df[usable]
    kreis_rate = (with_svb.groupby("kreis")["svb_wohn"].sum()
                  / with_svb.groupby("kreis")["population"].sum())
    global_rate = float(with_svb["svb_wohn"].sum() / with_svb["population"].sum()) \
        if len(with_svb) else 0.0

    n = len(df)
    n_primary = int(usable.sum())
    n_fallback = n - n_primary
    fallback_rate = df["kreis"].map(kreis_rate).fillna(global_rate)
    production = df["svb_wohn"].where(usable, fallback_rate * df["population"])

    share = n_fallback / n if n else 0.0
    warn_prefix = "WARNING: " if share > warn_share else ""
    print(
        f"[braunschweig.gravity.production_mass] {warn_prefix}work production "
        f"mass = svb_wohn: primary (own svb_wohn) {n_primary}/{n} "
        f"({100.0 * n_primary / n if n else 0.0:.1f}%), fallback "
        f"(Kreis-mean rate x population) {n_fallback}/{n} ({100.0 * share:.1f}%)"
    )
    return pd.DataFrame({
        "origin_id": df["origin_id"],
        "population": production.astype(float),
    })


def tilt_taz_production_by_gemeinde_rate(pop_taz: pd.DataFrame,
                                         df_population_gemeinde: pd.DataFrame,
                                         df_svb: pd.DataFrame,
                                         warn_share: float = SVB_FALLBACK_WARN_SHARE) -> pd.DataFrame:
    """Scale each TAZ's production by its parent Gemeinde's employment rate.

    svb_wohn carries NO sub-Gemeinde information, so the tilt changes only
    the BETWEEN-Gemeinde masses and preserves the within-Gemeinde home-point
    distribution: production_taz = population_taz * (svb_wohn_gem / pop_gem).
    Reuses ``build_work_production_mass`` for the Gemeinde-level masses (and
    thereby its fallback + logging semantics).
    """
    gem_production = build_work_production_mass(
        df_population_gemeinde, df_svb, mode="svb_wohn", warn_share=warn_share,
    ).rename(columns={"population": "gem_production"})
    gem = df_population_gemeinde.rename(
        columns={"population": "gem_population"}
    ).merge(gem_production, on="origin_id", how="left")
    gem["rate"] = gem["gem_production"] / gem["gem_population"]

    out = pop_taz.copy()
    out["commune_id"] = out["commune_id"].astype(str)
    out = out.merge(
        gem[["origin_id", "rate"]].rename(columns={"origin_id": "commune_id"}),
        on="commune_id", how="left",
    )
    n_missing = int(out["rate"].isna().sum())
    if n_missing:
        # A TAZ whose commune is absent from the Gemeinde population frame
        # cannot be tilted -- keep its population mass and log it.
        print(
            f"[braunschweig.gravity.production_mass] WARNING: {n_missing} TAZ "
            "without a Gemeinde rate keep their population mass"
        )
    out["population"] = out["population"] * out["rate"].fillna(1.0)
    return out[["taz_id", "commune_id", "population"]]
