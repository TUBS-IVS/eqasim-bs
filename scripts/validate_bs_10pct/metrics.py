"""Population, household, employment, and commute KPIs for ZGB-8."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import io, references
from .config import (
    DISTANCE_BINS, DISTANCE_LABELS, DURATION_BINS, DURATION_LABELS,
    MID_BASELINE, SAMPLING_RATE, ZGB8,
)


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------
def population_per_kreis() -> pd.DataFrame:
    persons = io.persons_with_kreis()
    synth = persons.groupby("ars5").size().rename("synth_sample").reset_index()
    synth["synth_expanded"] = synth["synth_sample"] / SAMPLING_RATE
    zensus = references.load_zensus_population()
    out = zensus.merge(synth, on="ars5", how="left").fillna({"synth_sample": 0, "synth_expanded": 0})
    out["deviation_pct"] = (out["synth_expanded"] - out["zensus_2022"]) / out["zensus_2022"] * 100
    total = pd.DataFrame({
        "ars5": ["TOTAL"], "kreis_name": ["ZGB-8"],
        "zensus_2022": [out["zensus_2022"].sum()],
        "synth_sample": [out["synth_sample"].sum()],
        "synth_expanded": [out["synth_expanded"].sum()],
        "deviation_pct": [
            (out["synth_expanded"].sum() - out["zensus_2022"].sum())
            / out["zensus_2022"].sum() * 100
        ],
    })
    return pd.concat([out, total], ignore_index=True)


def age_sex_pyramid() -> pd.DataFrame:
    persons = io.load_persons()
    bins = list(range(0, 100, 5)) + [200]
    labels = [f"{b}-{b+4}" for b in bins[:-2]] + ["95+"]
    persons = persons.assign(age_bin=pd.cut(persons["age"], bins=bins, labels=labels, right=False))
    pyr = persons.groupby(["age_bin", "sex"], observed=True).size().rename("count").reset_index()
    total = pyr["count"].sum()
    pyr["share"] = pyr["count"] / total
    return pyr


def household_size_per_kreis() -> pd.DataFrame:
    """Compare HH-size shares per Kreis vs. Zensus 2022.

    households.csv carries the assigned household_size as a categorical
    string ("1","2","3","4","5+"). We join the home Kreis via homes.gpkg.
    """
    hh = io.load_households()[["household_id", "household_size"]].copy()
    hh["size_bin"] = hh["household_size"].astype(str)
    hh = hh.merge(io.homes_with_kreis(), on="household_id", how="left")
    hh = hh[hh["ars5"].isin(ZGB8.keys())]
    synth = hh.groupby(["ars5", "size_bin"]).size().rename("synth_count").reset_index()
    totals = synth.groupby("ars5")["synth_count"].transform("sum")
    synth["synth_share"] = synth["synth_count"] / totals
    zensus = references.load_zensus_households()
    out = synth.merge(
        zensus[["ars5", "size_bin", "share"]].rename(columns={"share": "zensus_share"}),
        on=["ars5", "size_bin"], how="outer",
    ).fillna(0)
    out["deviation_pp"] = (out["synth_share"] - out["zensus_share"]) * 100
    return out.sort_values(["ars5", "size_bin"]).reset_index(drop=True)


def employment_summary() -> pd.DataFrame:
    persons = io.persons_with_kreis()
    persons["age_group"] = pd.cut(
        persons["age"],
        bins=[0, 14, 17, 24, 64, 200],
        labels=["<15", "15-17", "18-24", "25-64", "65+"],
        right=True,
    )
    grouped = (
        persons.groupby(["ars5", "age_group"], observed=True)
        .agg(n=("person_id", "count"),
             employed=("employed", "sum"),
             license=("has_driving_license", "sum"),
             pt_sub=("has_pt_subscription", "sum"))
        .reset_index()
    )
    grouped["employment_rate"] = grouped["employed"] / grouped["n"]
    grouped["license_rate"] = grouped["license"] / grouped["n"]
    grouped["pt_sub_rate"] = grouped["pt_sub"] / grouped["n"]
    return grouped


# ---------------------------------------------------------------------------
# Commute (home → primary work)
# ---------------------------------------------------------------------------
def commute_distance_summary() -> pd.DataFrame:
    """Crow-fly commute distance distribution per home Kreis.

    Adds MiD P13 ``mittel`` (person-weighted MiD reference mean per
    Kreis) plus ``deviation_km`` and ``deviation_pct`` so the report
    flags mismatches against the actual MiD reference (which is the
    target the synthesis is sampled from via P13-CDF override).
    """
    commutes = io.load_commutes_gdf()
    homes = io.homes_with_kreis()
    persons = io.load_persons()[["person_id", "household_id", "employed"]]
    df = (
        commutes[["person_id", "distance_km"]]
        .merge(persons, on="person_id", how="inner")
        .merge(homes, on="household_id", how="left")
    )
    df["dist_bin"] = pd.cut(df["distance_km"], bins=DISTANCE_BINS, labels=DISTANCE_LABELS, right=False)
    summary = df.groupby("ars5").agg(
        n_commuters=("person_id", "count"),
        mean_km=("distance_km", "mean"),
        median_km=("distance_km", "median"),
        p90_km=("distance_km", lambda s: s.quantile(0.9)),
    ).reset_index()

    # Attach MiD P13 ``mittel`` reference (person-weighted mean km).
    mid_p13 = references.load_mid().get("P13")
    if mid_p13 is not None and "mittel" in mid_p13.columns:
        ref = mid_p13[["ars5", "mittel"]].rename(columns={"mittel": "mid_mean_km"})
        summary = summary.merge(ref, on="ars5", how="left")
        summary["deviation_km"] = summary["mean_km"] - summary["mid_mean_km"]
        summary["deviation_pct"] = np.where(
            summary["mid_mean_km"] > 0,
            summary["deviation_km"] / summary["mid_mean_km"] * 100,
            np.nan,
        )
    return summary


def commute_distance_distribution() -> pd.DataFrame:
    """Histogram of commute distance bins (overall)."""
    commutes = io.load_commutes_gdf()
    df = commutes.copy()
    df["dist_bin"] = pd.cut(df["distance_km"], bins=DISTANCE_BINS, labels=DISTANCE_LABELS, right=False)
    out = df.groupby("dist_bin", observed=True).size().rename("synth_count").reset_index()
    out["synth_share"] = out["synth_count"] / out["synth_count"].sum()
    # MiD P13 overall (commute) — first row of regional table.
    mid = references.load_mid().get("P13")
    if mid is not None and "ars5" in mid.columns:
        # Heuristic: MiD shares come as columns dist_lt1, dist_1_2 ... pick "Beruf" rows if present.
        pass
    return out


def commute_od_kreis() -> pd.DataFrame:
    """Synth Kreis→Kreis commute matrix vs. BA Pendleratlas."""
    commutes = io.load_commutes_gdf().copy()
    homes = io.homes_with_kreis()
    persons = io.load_persons()[["person_id", "household_id"]]
    commutes = commutes.merge(persons, on="person_id", how="inner").merge(homes, on="household_id", how="left")
    commutes = commutes.rename(columns={"ars5": "orig_ars"})

    # Determine destination Kreis from end of LineString.
    kreise = io.load_kreise_gdf()
    import geopandas as gpd
    end_pts = gpd.GeoDataFrame(
        {"person_id": commutes["person_id"], "orig_ars": commutes["orig_ars"]},
        geometry=commutes.geometry.apply(lambda g: g.boundary.geoms[-1] if g and g.geom_type == "LineString" else None),
        crs=commutes.crs,
    ).dropna(subset=["geometry"])
    joined = gpd.sjoin(end_pts, kreise[["ars5", "geometry"]], how="left", predicate="within")
    joined = joined.rename(columns={"ars5": "dest_ars"})
    joined["dest_ars"] = joined["dest_ars"].fillna("EXTERN")

    synth = joined.groupby(["orig_ars", "dest_ars"]).size().rename("synth_flow").reset_index()
    synth["synth_flow_expanded"] = synth["synth_flow"] / SAMPLING_RATE

    ba = references.load_pendler()
    ba = ba[ba["orig_ars"].isin(ZGB8) | ba["dest_ars"].isin(ZGB8)]

    out = synth.merge(ba, on=["orig_ars", "dest_ars"], how="outer").fillna(0)
    out = out.rename(columns={"flow": "ba_flow"})
    out["deviation_pct"] = np.where(
        out["ba_flow"] > 0,
        (out["synth_flow_expanded"] - out["ba_flow"]) / out["ba_flow"] * 100,
        np.nan,
    )
    return out.sort_values("ba_flow", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Mode share (pulls from MATSim XML via io.trips_full)
# ---------------------------------------------------------------------------
def mode_share_overall() -> pd.DataFrame:
    trips = io.trips_full()
    trips["mid_mode"] = trips["mode"].map(io.map_mode)
    counts = trips["mid_mode"].value_counts(normalize=True).rename("synth_share")
    # pandas <2.0 named the index column "index"; pandas >=2.0 keeps the
    # Series-index name ("mid_mode"). Rename both spellings to "mode".
    df = counts.reset_index().rename(columns={"index": "mode", "mid_mode": "mode"})
    df["mid_share"] = df["mode"].map(MID_BASELINE["mode_share"])
    df["deviation_pp"] = (df["synth_share"] - df["mid_share"]) * 100
    return df


def mode_share_by_purpose() -> pd.DataFrame:
    """Mode share stratified by raw eqasim/ENTD ``following_purpose``.

    Each leg is counted with the activity it ends at; return-home legs keep
    ``home`` as their purpose (consistent with the ENTD chain definition
    used throughout the model).
    """
    trips = io.trips_full().copy()
    trips["mid_mode"] = trips["mode"].map(io.map_mode)
    out = (
        trips.groupby(["following_purpose", "mid_mode"]).size()
        .rename("count").reset_index()
    )
    totals = out.groupby("following_purpose")["count"].transform("sum")
    out["share"] = out["count"] / totals
    return out


def mode_share_by_distance() -> pd.DataFrame:
    trips = io.trips_full()
    trips["mid_mode"] = trips["mode"].map(io.map_mode)
    trips["dist_bin"] = pd.cut(trips["distance_km"], bins=DISTANCE_BINS, labels=DISTANCE_LABELS, right=False)
    out = trips.groupby(["dist_bin", "mid_mode"], observed=True).size().rename("count").reset_index()
    totals = out.groupby("dist_bin", observed=True)["count"].transform("sum")
    out["share"] = out["count"] / totals
    return out


# ---------------------------------------------------------------------------
# Travel KPIs (all purposes)
# ---------------------------------------------------------------------------
def trip_summary() -> dict:
    trips = io.trips_full()
    persons = io.load_persons()
    n_persons = len(persons)
    n_trips = len(trips)
    return {
        "n_persons": n_persons,
        "n_trips": n_trips,
        "trips_per_person": n_trips / n_persons,
        "mean_distance_km": float(trips["distance_km"].mean()),
        "median_distance_km": float(trips["distance_km"].median()),
        "mean_duration_min": float(trips["travel_time_min"].mean()),
        "daily_distance_km": float(trips.groupby("person_id")["distance_km"].sum().mean()),
        "mid_baseline": MID_BASELINE,
    }


def trip_distance_distribution() -> pd.DataFrame:
    trips = io.trips_full()
    trips["dist_bin"] = pd.cut(trips["distance_km"], bins=DISTANCE_BINS, labels=DISTANCE_LABELS, right=False)
    out = trips.groupby("dist_bin", observed=True).size().rename("count").reset_index()
    out["share"] = out["count"] / out["count"].sum()
    return out


def trip_duration_distribution() -> pd.DataFrame:
    trips = io.trips_full()
    trips["dur_bin"] = pd.cut(trips["travel_time_min"], bins=DURATION_BINS, labels=DURATION_LABELS, right=False)
    out = trips.groupby("dur_bin", observed=True).size().rename("count").reset_index()
    out["share"] = out["count"] / out["count"].sum()
    return out


def departure_profile() -> pd.DataFrame:
    trips = io.trips_full()
    out = trips.groupby("departure_hour").size().rename("count").reset_index()
    out["share"] = out["count"] / out["count"].sum()
    return out


def purpose_mix() -> pd.DataFrame:
    """Raw eqasim/ENTD purpose mix.

    Each trip is counted under the activity it ends at
    (``following_purpose``). A chain ``home -> work -> home`` therefore
    contributes one ``work`` and one ``home`` trip (the start-from-home is
    not a trip). This matches the ENTD chain convention that the entire
    pipeline operates on; no relabelling of return-home legs is performed.
    """
    trips = io.trips_full().copy()
    out = trips["following_purpose"].value_counts(normalize=True).rename("synth_share").reset_index()
    # pandas <2.0 → "index"; pandas >=2.0 → "following_purpose".
    out = out.rename(columns={"index": "purpose", "following_purpose": "purpose"})
    out["mid_share"] = out["purpose"].map(MID_BASELINE["purpose_mix"])
    out["deviation_pp"] = (out["synth_share"] - out["mid_share"]) * 100
    return out


def purpose_mix_raw() -> pd.DataFrame:
    """Deprecated alias of :func:`purpose_mix`.

    The R-D ``home -> preceding_purpose`` remap was removed; ``purpose_mix``
    now returns the raw ENTD distribution, so this function is identical
    to it. Kept for backward compatibility with the ``purpose_mix_raw``
    JSON key written by :mod:`report`.
    """
    return purpose_mix()


def purpose_mix_w1_baseline(present_purposes) -> dict:
    """MiD 2023 W1 purpose-mix baseline, selected by the presence of the
    ``escort`` purpose in the synthetic distribution (issue #201).

    The static ``purpose_mix_w1`` splits Begleitung (``escort``, 8/99) out of
    Erledigung (``other``, 11/99). That split is only apples-to-apples with a
    population built with ``escort_purpose`` ON, which carries a dedicated
    ``escort`` purpose. On a flag-OFF population Begleitung is folded into
    ``other`` (eqasim has no ``escort``), so the escort share must be folded
    back into ``other`` here too — otherwise the flag-OFF ``other`` value
    already contains Begleitung but is scored against 11/99, inflating its
    deviation by ~8 pp and reporting a spurious ``escort`` gap. Mirrors the
    presence-based selection in ``trip_coherence.scored_mid_purposes``.

    Returns a fresh dict; the shared static baseline is never mutated.
    """
    baseline = dict(MID_BASELINE["purpose_mix_w1"])
    if "escort" not in present_purposes:
        baseline["other"] = baseline["other"] + baseline.pop("escort")
    return baseline


def purpose_mix_w1_active_baseline(present_purposes) -> dict:
    """Active-adjusted MiD 2023 W1 baseline for ``escort_passive_education``
    (issue #256), presence-guarded exactly like ``purpose_mix_w1_baseline``.

    The static ``purpose_mix_w1`` Begleitung share (8/99) mixes the MiD
    active leg (W_ZWECK 6, Bringen/Holen) and passive leg (W_ZWECK 13, the
    escorted person's own leg). Under ``escort_passive_education`` the
    model's ``escort`` purpose captures only the ACTIVE leg; the passive
    leg is counted as ``education`` in the synthetic population. Comparing
    such a population against the unmodified (mixed) Begleitung share
    would inflate the apparent ``escort`` gap and understate ``education``,
    so this baseline redistributes the Begleitung mass the same way:

        escort_active     = purpose_mix_w1["escort"] * active_share
        education_adjusted = purpose_mix_w1["education"]
                              + purpose_mix_w1["escort"] * (1 - active_share)

    ``active_share`` is the W_GEW-weighted share of active (W_ZWECK 6) legs
    among MiD escort legs, read from the pinned
    ``mid2023_escort_w_zweck_split.csv`` via
    ``references.escort_active_share`` (never hardcoded).

    If ``"escort"`` is NOT in ``present_purposes`` there is no active-only
    escort category in the synthesis to justify the split -- applying it
    anyway would silently score ``other`` against the unfolded 11/99 and
    ``education`` against an inflated share with no signal that the
    adjustment does not apply. In that case this function returns exactly
    ``purpose_mix_w1_baseline(present_purposes)`` (the presence-folded 19/99
    baseline) instead, so the active-adjusted table degrades to the same
    numbers as the raw table rather than a spurious active-adjusted one.

    config.py stays static (no CSV loading at import time), so this dict is
    built lazily on every call from the static ``purpose_mix_w1`` baseline;
    the underlying share is cached in ``references.escort_active_share``.
    Mass-preserving: ``sum(result.values()) == sum(purpose_mix_w1.values())``.
    Returns a fresh dict; the shared static baseline is never mutated.
    """
    if "escort" not in present_purposes:
        return purpose_mix_w1_baseline(present_purposes)
    raw = MID_BASELINE["purpose_mix_w1"]
    active_share = references.escort_active_share()
    adjusted = dict(raw)
    adjusted["escort"] = raw["escort"] * active_share
    adjusted["education"] = raw["education"] + raw["escort"] * (1.0 - active_share)
    return adjusted


def purpose_mix_no_home() -> pd.DataFrame:
    """Purpose mix excluding ``home`` legs, normalised to 100 %.

    Compared against MiD 2023 W1 (``analog MiD 2008``) which uses the
    same convention: Heimwege werden auf den vorherigen Weg-Zweck
    zurück-gemapped und tauchen nicht als eigene Kategorie auf. This is
    the literature-standard view of trip-purpose distribution and the
    correct comparison for the model.

    Source: ``eqasim-data/data/braunschweig/mid/mid2023_W1.csv``
    (Gesamt = 03ZGB).  Mapping eqasim → W1:
      work       ← Arbeit + Dienst
      education  ← Ausbildung
      shop       ← Einkauf
      other      ← Erledigung   (+ Begleitung when escort_purpose is OFF)
      escort     ← Begleitung    (only when escort_purpose is ON, issue #201)
      leisure    ← Freizeit

    The baseline is chosen presence-based (see ``purpose_mix_w1_baseline``):
    on a flag-OFF population Begleitung is folded back into ``other`` so the
    comparison stays apples-to-apples.
    """
    trips = io.trips_full().copy()
    trips = trips[trips["following_purpose"] != "home"]
    if len(trips) == 0:
        return pd.DataFrame(columns=["purpose", "synth_share", "mid_share", "deviation_pp"])
    out = trips["following_purpose"].value_counts(normalize=True).rename("synth_share").reset_index()
    out = out.rename(columns={"index": "purpose", "following_purpose": "purpose"})
    baseline = purpose_mix_w1_baseline(set(out["purpose"]))
    out["mid_share"] = out["purpose"].map(baseline)
    out["deviation_pp"] = (out["synth_share"] - out["mid_share"]) * 100
    return out.sort_values("synth_share", ascending=False).reset_index(drop=True)


def purpose_mix_no_home_active() -> pd.DataFrame:
    """Purpose mix excluding ``home`` legs, scored against the
    active-adjusted MiD 2023 W1 baseline (issue #256,
    ``escort_passive_education``).

    Mirrors :func:`purpose_mix_no_home` exactly (same filtering, same
    columns, same presence-based baseline selection via
    ``set(out["purpose"])``), but maps the synthetic distribution against
    :func:`purpose_mix_w1_active_baseline` instead of
    :func:`purpose_mix_w1_baseline`. This is the correct reference when the
    synthesis was built with ``escort_passive_education`` ON, where the
    model's ``escort`` purpose is active-only and passive escort legs
    surface as ``education`` trips. On an escort-absent population
    :func:`purpose_mix_w1_active_baseline` itself degrades to the
    presence-folded baseline, so this table then matches section 5.3
    exactly instead of scoring against a spurious active-adjusted
    reference.

    This validation report is post-hoc (it only reads output CSVs/XML and
    cannot detect which flag state produced the population), so both this
    table (report section 5.3b) and the raw-baseline table (section 5.3,
    :func:`purpose_mix_no_home`) are rendered side by side, clearly
    labelled; the reader picks the reference matching their run
    configuration.
    """
    trips = io.trips_full().copy()
    trips = trips[trips["following_purpose"] != "home"]
    if len(trips) == 0:
        return pd.DataFrame(columns=["purpose", "synth_share", "mid_share", "deviation_pp"])
    out = trips["following_purpose"].value_counts(normalize=True).rename("synth_share").reset_index()
    out = out.rename(columns={"index": "purpose", "following_purpose": "purpose"})
    baseline = purpose_mix_w1_active_baseline(set(out["purpose"]))
    out["mid_share"] = out["purpose"].map(baseline)
    out["deviation_pp"] = (out["synth_share"] - out["mid_share"]) * 100
    return out.sort_values("synth_share", ascending=False).reset_index(drop=True)


def mobility_quote() -> dict:
    """Share of persons with at least one trip on the simulation day.

    Compared against MiD 2023 P36.1 (``Mobilität am Stichtag``,
    Basis = alle Personen).  Source:
    ``eqasim-data/data/braunschweig/mid/mid2023_P36_1.csv``.

    Returns dict with::

        {
          "synth_total":   <synth share>,
          "mid_total":     0.80,
          "deviation_pp":  <synth - mid> * 100,
          "per_kreis": [   # one row per ZGB-8 Kreis
            {"ars5", "kreis_name", "synth_share", "mid_share", "deviation_pp"},
            ...
          ],
        }
    """
    persons = io.persons_with_kreis()[["person_id", "ars5"]]
    trips = io.load_trips()[["person_id"]]
    mobile_ids = set(trips["person_id"].unique())
    persons = persons.copy()
    persons["mobile"] = persons["person_id"].isin(mobile_ids).astype(int)

    total_share = float(persons["mobile"].mean())
    mid_total = MID_BASELINE["mobility_quote"]

    per_kreis_rows = []
    grouped = persons[persons["ars5"].isin(ZGB8.keys())].groupby("ars5")
    for ars5, g in grouped:
        synth_share = float(g["mobile"].mean())
        mid_share = MID_BASELINE["mobility_quote_per_kreis"].get(ars5)
        per_kreis_rows.append({
            "ars5": ars5,
            "kreis_name": ZGB8.get(ars5, ars5),
            "n_persons": int(len(g)),
            "synth_share": synth_share,
            "mid_share": mid_share,
            "deviation_pp": (synth_share - mid_share) * 100 if mid_share is not None else None,
        })

    return {
        "synth_total": total_share,
        "mid_total": mid_total,
        "deviation_pp": (total_share - mid_total) * 100,
        "per_kreis": per_kreis_rows,
    }


def activity_chains_top(n: int = 15) -> pd.DataFrame:
    """Top activity chains across the day."""
    trips = io.load_trips()
    chains = (
        trips.sort_values(["person_id", "trip_index"])
        .groupby("person_id").apply(
            lambda g: "->".join(["home"] + g["following_purpose"].tolist())
        )
        .rename("chain")
        .reset_index()
    )
    counts = chains["chain"].value_counts().head(n).rename("count").reset_index()
    # pandas <2.0 produces ["index", "count"]; pandas >=2.0 produces ["chain", "count"].
    counts = counts.rename(columns={"index": "chain"})
    counts["share"] = counts["count"] / len(chains)
    return counts
