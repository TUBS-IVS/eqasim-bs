"""Collect the measured outputs of the October run (old) and the full rerun (new) into the four data files
of the report dashboard (issue #648).

Nothing is measured here: every value is copied or re-aggregated from a committed output folder:
    diag_<run>/      python -m braunschweig.analysis.popsim_batch_diagnostics.run (same commit for both runs)
    cmp/             compare_runs.py diag_old diag_new cmp
    zones_<run>/     zone_views.py <popsim_work> zones_<run>
    popval_<run>/    python -m braunschweig.analysis.population_validation.run_population_validation
    analysis_<run>/  <output>/analysis of the run (synthesis analysis stages of the pipeline)
    run_facts.csv    hand-collected run facts with their source (log line or run manifest)

Usage:
    python build_dashboard_data.py IN_DIR OUT_DIR

Writes controls.csv, facts.csv, cells_1km.csv and plausibility.csv (columns documented per function).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RUNS = ("old", "new")
KREIS_NAMES = {"03101": "Braunschweig", "03102": "Salzgitter", "03103": "Wolfsburg", "03151": "Gifhorn",
               "03153": "Goslar", "03154": "Helmstedt", "03157": "Peine", "03158": "Wolfenbüttel",
               "zgb": "Großraum (ZGB)"}
GROUP_LABELS = {
    "household_total": "Haushalte insgesamt", "household_size": "Haushaltsgröße",
    "household_type": "Haushaltstyp / Kinder", "tenure": "Eigentum / Miete", "building_type": "Gebäudetyp",
    "age_sex": "Alter × Geschlecht", "employed_grid": "Erwerbstätige nach Alter", "own_cars": "Autos je Haushalt",
    "own_bikes": "Fahrräder je Haushalt", "kreis:beruflabschluss": "Berufsabschluss",
    "kreis:economic_status": "Ökonomischer Status", "kreis:education": "Bildungseinrichtung besucht",
    "kreis:employed": "Erwerbstätige gesamt", "kreis:employment_status": "Erwerbsstatus",
    "kreis:escort_participation": "Begleitweg am Tag (SrV)", "kreis:has_ebike": "E-Bike im Haushalt",
    "kreis:leisure_participation": "Freizeitweg am Tag (SrV)", "kreis:number_of_bicycles": "Fahrräder (Kreis)",
    "kreis:number_of_cars": "Autos (Kreis)", "kreis:pt_ticket_group4": "ÖV-Ticket",
    "kreis:schulabschluss": "Schulabschluss", "kreis:trip_class": "Wege am Werktag (SrV)",
    "kreis:work_by_employment": "Arbeitsweg am Tag (SrV)"}
GEOGRAPHY_LABELS = {"ZENSUS100m": "100 m", "ZENSUS1km": "1 km", "KREIS": "Kreis"}
CATEGORY_WORDS = {
    "none": "keiner", "tertiary": "Hochschule", "vocational": "beruflich", "high": "hoch", "low": "niedrig",
    "medium": "mittel", "very_high": "sehr hoch", "very_low": "sehr niedrig", "mid": "mittel",
    "yes": "ja", "no": "nein", "0": "0", "1": "1", "2": "2", "3": "3", "3plus": "3+", "4plus": "4+",
    "deutschlandticket": "Deutschlandticket", "never_pt": "nie ÖV", "occasional_ticket": "gelegentlich",
    "other_flatrate": "andere Zeitkarte", "1_2": "1–2", "3_4": "3–4", "5plus": "5+",
    "employed_work": "erwerbstätig, mit Arbeitsweg", "employed_nowork": "erwerbstätig, ohne Arbeitsweg",
    "nonemployed_work": "nicht erwerbstätig, mit Arbeitsweg",
    "nonemployed_nowork": "nicht erwerbstätig, ohne Arbeitsweg", "vollzeit": "Vollzeit", "teilzeit": "Teilzeit",
    "geringfuegig": "geringfügig", "in_ausbildung": "in Ausbildung", "nicht_erwerbstaetig": "nicht erwerbstätig",
    "sonstiges": "sonstiges", "erwerbstaetig_unspec": "erwerbstätig o. A.",
    "0_5_edu": "0–5 J., mit Einrichtung", "0_5_noedu": "0–5 J., ohne", "6_17_edu": "6–17 J., mit Einrichtung",
    "6_17_noedu": "6–17 J., ohne", "18plus_edu": "18+ J., mit Einrichtung", "18plus_noedu": "18+ J., ohne"}


def _age(text: str) -> str:
    return text.replace("_plus", "+").replace("plus", "+").replace("_", "–")


def control_label(control: str, group: str) -> str:
    """Readable German name of a control column (for the page only; the id stays in the data)."""
    name = control.replace("_target", "")
    if m := re.match(r"^([FM])_AGE_(.+?)_agg", name):
        return f"{'Frauen' if m.group(1) == 'F' else 'Männer'} {_age(m.group(2))}"
    if m := re.match(r"^EMPLOYED_([FM])_(.+?)_agg", name):
        return f"Erwerbstätige {'Frauen' if m.group(1) == 'F' else 'Männer'} {_age(m.group(2))}"
    if m := re.match(r"^(\d)_Person(en)?(_und_mehr)?_", name):
        return f"Haushalte mit {m.group(1)}{'+' if m.group(3) else ''} Person{'en' if m.group(1) != '1' else ''}"
    if name.startswith("Insgesamt_Haushalte"):
        return "Haushalte insgesamt"
    fixed = {"EigentuemerHH": "Eigentümerhaushalte", "MieterHH": "Mieterhaushalte",
             "Paare_mitKind": "Paare mit Kind(ern), jedes Alter", "Paare_ohneKind": "Paare ohne Kind",
             "Alleinerziehende": "Alleinerziehende, Kinder jedes Alters",
             "MehrpersHHohneKernfam": "Mehrpersonenhaushalte ohne Kernfamilie",
             "HH_MINOR_MULTI_ADULT": "mit Minderjährigen, 2+ Erwachsene",
             "HH_MINOR_ONE_ADULT": "mit Minderjährigen, 1 Erwachsener",
             "building_type_ein_zweifamilienhaus": "Ein-/Zweifamilienhaus",
             "building_type_mehrfamilienhaus": "Mehrfamilienhaus", "building_type_sonstiges": "sonstiges Gebäude",
             "employed_KREIS": "Erwerbstätige"}
    for key, label in fixed.items():
        if name.startswith(key):
            return label
    if m := re.match(r"^OWN_(CARS|BIKES)_(.+?)_agg", name):
        return f"{m.group(2).replace('plus', '+')} {'Autos' if m.group(1) == 'CARS' else 'Fahrräder'}"
    if group.startswith("kreis:"):
        family = group.split(":", 1)[1]
        category = name[len(family) + 1:].replace("_KREIS", "")
        return f"{GROUP_LABELS.get(group, family)}: {CATEGORY_WORDS.get(category, category)}"
    return name


def build_controls(in_dir: Path) -> pd.DataFrame:
    """controls.csv: one row per control and geography (region rows of both runs' control fit)."""
    fit = pd.read_csv(in_dir / "cmp" / "fit_by_control.csv")
    fit.insert(0, "label", [control_label(c, g) for c, g in zip(fit["control"], fit["group"])])
    fit.insert(2, "group_label", fit["group"].map(GROUP_LABELS).fillna(fit["group"]))
    fit.insert(4, "level", fit["geography"].map(GEOGRAPHY_LABELS))
    fit["wape_change"] = fit["wape_new"] - fit["wape_old"]
    fit["relative_error_old"] = fit["synthetic_sum_old"] / fit["target_sum_old"] - 1.0
    fit["relative_error_new"] = fit["synthetic_sum_new"] / fit["target_sum_new"] - 1.0
    keep = ["label", "control", "group_label", "group", "level", "geography", "presence",
            "target_sum_old", "synthetic_sum_old", "wape_old", "relative_error_old",
            "target_sum_new", "synthetic_sum_new", "wape_new", "relative_error_new", "wape_change"]
    out = fit[keep].replace([np.inf, -np.inf], np.nan)
    out = out[~((out["target_sum_old"].fillna(0) == 0) & (out["target_sum_new"].fillna(0) == 0))]
    return out.reset_index(drop=True)


class Facts(list):
    """Rows of facts.csv: id, section, item, sub, label, old, new, reference, unit, note, source."""

    def add(self, section, item, sub, label, old, new, reference=np.nan, unit="", note="", source=""):
        self.append({"id": f"{section}|{item}|{sub}", "section": section, "item": item, "sub": sub,
                     "label": label, "old": old, "new": new, "reference": reference, "unit": unit,
                     "note": note, "source": source})


def _region_reuse(in_dir: Path, run: str) -> pd.Series:
    reuse = pd.read_csv(in_dir / f"diag_{run}" / "reuse.csv")
    return reuse[reuse["scope"] == "region"].iloc[0]


def build_facts(in_dir: Path) -> pd.DataFrame:
    facts = Facts()
    head = pd.read_csv(in_dir / "cmp" / "headline.csv").set_index("metric")
    src_diag = "popsim_batch_diagnostics (diag_old / diag_new)"
    labels = {"reuse.n_synthetic_households": ("Haushalte gesamt", "Haushalte"),
              "reuse.n_distinct_used": ("Genutzte MiD-Haushalte", "Haushalte"),
              "reuse.distinct_share_of_seed": ("Anteil genutzter MiD-Haushalte", "Anteil"),
              "reuse.kish_n_eff": ("Effektive Stichprobengröße (Kish)", "Haushalte"),
              "reuse.max_copies": ("Kopien des häufigsten Haushalts", "Kopien"),
              "reuse.top100_share": ("Anteil der 100 häufigsten Vorlagen", "Anteil"),
              "reuse.top1000_share": ("Anteil der 1.000 häufigsten Vorlagen", "Anteil"),
              "reuse.top5000_share": ("Anteil der 5.000 häufigsten Vorlagen", "Anteil"),
              "weight_class.max_abs_gap_pp": ("Größte Abweichung einer Gewichtsklasse", "Prozentpunkte"),
              "solver.non_simultaneous_rate.KREIS": ("Notlösung beim Runden, Kreis", "Anteil"),
              "solver.non_simultaneous_rate.ZENSUS1km": ("Notlösung beim Runden, 1 km", "Anteil"),
              "solver.non_simultaneous_rate.ZENSUS100m": ("Notlösung beim Runden, 100 m", "Anteil"),
              "solver.not_solved_calls": ("Rundungen ohne Lösung", "Aufrufe")}
    for metric, (label, unit) in labels.items():
        facts.add("kpi", metric, "", label, head.at[metric, "old"], head.at[metric, "new"], unit=unit,
                  source=src_diag)
    facts.add("kpi", "n_seed_households", "", "MiD-Haushalte in der Stichprobe",
              *[_region_reuse(in_dir, run)["n_seed_households"] for run in RUNS], unit="Haushalte",
              source=src_diag + " reuse.csv")
    # Persons without a binary sex: the old run reports them as uncovered records of age_sex, the new run
    # (seed records resolved by WP3) as resolved raw codes 3/9; both are synthetic share / seed weight share.
    facts.add("kpi", "divers_ratio", "", "Überrepräsentation „divers/keine Angabe“",
              head.at["coverage_ratio.age_sex", "old"], head.at["resolved_ratio.HP_SEX_raw", "new"],
              reference=2.0, unit="Faktor",
              note="old: coverage age_sex (uncovered records); new: resolved HP_SEX 3/9; bound 2 = ASSUMPTION "
                   "of the diagnostics (coverage_ratio_high)", source=src_diag)
    for geography in ("ZENSUS100m", "ZENSUS1km", "KREIS"):
        values = []
        for run in RUNS:
            fit = pd.read_csv(in_dir / f"diag_{run}" / "control_fit.csv")
            row = fit[(fit["scope"] == "region_geography") & (fit["geography"] == geography)].iloc[0]
            values.append(row["wape"])
        facts.add("kpi", f"wape.{geography}", "", f"Abweichung aller Vorgaben, {GEOGRAPHY_LABELS[geography]}",
                  *values, unit="WAPE", note="pooled over all controls of the level; KREIS is an upper bound",
                  source=src_diag)
    minors = []
    for run in RUNS:
        comp = pd.read_csv(in_dir / f"diag_{run}" / "household_composition.csv")
        minors.append(comp["households_with_minor"].sum() / comp["households"].sum())
    facts.add("kpi", "households_with_minor", "", "Haushalte mit Minderjährigen", *minors, unit="Anteil",
              source=src_diag + " household_composition.csv")
    totals = pd.read_csv(in_dir / "cmp" / "household_totals_per_batch.csv")
    facts.add("kpi", "batches_off_target", "", "Teilgebiete mit falscher Haushaltszahl",
              int((totals["difference_old"] != 0).sum()), int((totals["difference_new"] != 0).sum()),
              unit="von 60", source="compare_runs household_totals_per_batch.csv")
    facts.add("kpi", "household_total_target", "", "Haushalte laut Vorgabe", totals["target_sum_old"].sum(),
              totals["target_sum_new"].sum(), unit="Haushalte", source="compare_runs household_totals_per_batch.csv")
    for _, row in totals.iterrows():
        facts.add("batch", row["batch"], "hh_diff", "Haushalte minus Vorgabe", row["difference_old"],
                  row["difference_new"], unit="Haushalte", source="compare_runs household_totals_per_batch.csv")

    # Fit per control group and level.
    groups = pd.read_csv(in_dir / "cmp" / "fit_by_group_geography.csv")
    for _, row in groups.iterrows():
        facts.add("group_fit", row["group"], row["geography"],
                  f"{GROUP_LABELS.get(row['group'], row['group'])} · {GEOGRAPHY_LABELS[row['geography']]}",
                  row["wape_old"], row["wape_new"], unit="WAPE",
                  note=f"controls old {row['n_controls_old']:.0f} / new {row['n_controls_new']:.0f}"
                  if pd.notna(row["n_controls_old"]) and pd.notna(row["n_controls_new"]) else
                  ("only in the new run" if pd.isna(row["n_controls_old"]) else "only in the October run"),
                  source="compare_runs fit_by_group_geography.csv")

    rare = pd.read_csv(in_dir / "cmp" / "rare_groups.csv")
    rare_labels = {"households with 6+ persons": "Haushalte mit 6+ Personen",
                   "persons aged 18-19": "Personen 18–19 Jahre",
                   "single-adult households with minors": "Alleinerziehende*"}
    for _, row in rare.iterrows():
        item = rare_labels[row["rare_group"]]
        for sub, label, unit in (("target", "Vorgabe", "Personen/Haushalte"),
                                 ("synthetic", "Ergebnis", "Personen/Haushalte"),
                                 ("relative_error", "regionsweit", "Anteil"), ("wape", "in den Zellen", "WAPE")):
            facts.add("rare", item, sub, label, row[f"{sub}_old"], row[f"{sub}_new"], unit=unit,
                      note=row["definition"], source="compare_runs rare_groups.csv")

    # Weight classes and survey sub-samples.
    for run_tables in (("weight_class_shares.csv", "weight_class"),):
        frames = {run: pd.read_csv(in_dir / f"diag_{run}" / run_tables[0]).set_index("weight_class")
                  for run in RUNS}
        for weight_class in frames["new"].index:
            for sub, label in (("seed_weight_share", "Anteil am MiD-Gewicht"),
                               ("synthetic_share", "Anteil der Kopien")):
                facts.add("weight_class", weight_class, sub, label, frames["old"].at[weight_class, sub],
                          frames["new"].at[weight_class, sub], unit="Anteil",
                          source=src_diag + " weight_class_shares.csv")
    attrs = {run: pd.read_csv(in_dir / f"diag_{run}" / "attribute_shares.csv") for run in RUNS}
    keyed = {run: frame.set_index(["attribute", frame["value"].astype(str)]) for run, frame in attrs.items()}
    for (attribute, value), row in keyed["new"].iterrows():
        old = keyed["old"]["synthetic_share"].get((attribute, value), np.nan)
        facts.add("attribute", attribute, value, f"{attribute} = {value}", old, row["synthetic_share"],
                  reference=row["seed_weight_share"], unit="Anteil",
                  note="reference = share of the MiD weight", source=src_diag + " attribute_shares.csv")

    # Donor concentration and copies histogram from the per-donor usage counts.
    usage = {run: pd.read_csv(in_dir / f"zones_{run}" / "donor_usage.csv")["copies"].sort_values(
        ascending=False).to_numpy() for run in RUNS}
    grid = np.unique(np.round(np.logspace(0, np.log10(max(len(u) for u in usage.values())), 120)).astype(int))
    for k in grid:
        values = [u[:k].sum() / u.sum() if k <= len(u) else 1.0 for u in usage.values()]
        facts.add("concentration", int(k), "cum_share", "Anteil der Kopien aus den k häufigsten Vorlagen",
                  *values, unit="Anteil", source="zone_views donor_usage.csv")
    bins = [(1, 1, "1"), (2, 2, "2"), (3, 5, "3–5"), (6, 10, "6–10"), (11, 50, "11–50"), (51, 100, "51–100"),
            (101, 500, "101–500"), (501, 10**9, "501+")]
    for low, high, label in bins:
        donor_share, copy_share = [], []
        for run in RUNS:
            u = usage[run]
            member = (u >= low) & (u <= high)
            donor_share.append(member.sum())
            copy_share.append(u[member].sum() / u.sum())
        facts.add("copies", label, "n_donors", "genutzte Vorlagen mit so vielen Kopien", *donor_share,
                  unit="Haushalte", source="zone_views donor_usage.csv")
        facts.add("copies", label, "copy_share", "Anteil an allen Kopien", *copy_share, unit="Anteil",
                  source="zone_views donor_usage.csv")

    # Per batch: distinct donors and Kish size.
    reuse = {run: pd.read_csv(in_dir / f"diag_{run}" / "reuse.csv") for run in RUNS}
    per_batch = {run: frame[frame["scope"] == "batch"].set_index("subject") for run, frame in reuse.items()}
    for batch in per_batch["new"].index:
        for sub, label in (("n_distinct_used", "genutzte Vorlagen"), ("kish_n_eff", "Kish-Größe"),
                           ("n_synthetic_households", "Haushalte")):
            facts.add("batch", batch, sub, label, per_batch["old"][sub].get(batch, np.nan),
                      per_batch["new"].at[batch, sub], unit="Haushalte", source=src_diag + " reuse.csv")
    kreis_rows = {run: frame[frame["scope"] == "kreis"].set_index("subject") for run, frame in reuse.items()
                  if "kreis" in set(frame["scope"])}
    if kreis_rows:
        for kreis in kreis_rows["new"].index:
            for sub, label in (("n_distinct_used", "genutzte Vorlagen"), ("kish_n_eff", "Kish-Größe")):
                facts.add("kreis_reuse", str(kreis).zfill(5), sub, label,
                          kreis_rows["old"][sub].get(kreis, np.nan), kreis_rows["new"].at[kreis, sub],
                          unit="Haushalte", source=src_diag + " reuse.csv")

    # Error by 100 m cell size.
    cellsize = {run: pd.read_csv(in_dir / f"zones_{run}" / "cellsize_bins.csv").set_index(
        ["group", "cell_households"]) for run in RUNS}
    for (group, label), row in cellsize["new"].iterrows():
        old = cellsize["old"]["wape"].get((group, label), np.nan)
        facts.add("cellsize", label, group, GROUP_LABELS.get(group, group), old, row["wape"], unit="WAPE",
                  note=f"cells new {row['n_cells']:.0f}", source="zone_views cellsize_bins.csv")
        old_cells = cellsize["old"]["n_cells"].get((group, label), np.nan)
        if group == "household_size":
            facts.add("cellsize_cells", label, "n_cells", "Zellen in der Klasse", old_cells, row["n_cells"],
                      unit="Zellen", source="zone_views cellsize_bins.csv")

    # Population validation (Zensus / DESTATIS / MiD references in the repo).
    quality = {run: pd.read_csv(in_dir / f"popval_{run}" / "quality_summary.csv").set_index("control")
               for run in RUNS}
    references = {"bev_share": "DESTATIS 12411-0018 (Bevölkerung je Kreis)", "sex": "DESTATIS 12411-0018",
                  "age_group": "DESTATIS 12411-0018", "household_size": "Zensus 2022, 1000A-2081",
                  "bicycles_per_hh": "MiD 2023 H12.3 je Kreis", "cars_per_hh": "MiD 2023 H7 je Kreis",
                  "employment_status": "MiD 2023 P9", "pt_ticket_type": "MiD 2023 P24.1",
                  "driving_license_type": "MiD 2023 P17.1", "employment": "MiD 2023 P9"}
    names = {"bev_share": "Bevölkerungsanteil je Kreis", "sex": "Geschlecht", "age_group": "Altersgruppen",
             "household_size": "Haushaltsgröße (Gemeinde)", "bicycles_per_hh": "Fahrräder je Haushalt",
             "cars_per_hh": "Autos je Haushalt", "employment_status": "Erwerbsstatus",
             "pt_ticket_type": "ÖV-Ticket", "driving_license_type": "Führerschein",
             "employment": "erwerbstätig ja/nein"}
    for control in quality["new"].index:
        row = quality["new"].loc[control]
        for sub, unit in (("mean_abs_delta_pp", "Prozentpunkte"), ("srmse", "SRMSE")):
            facts.add("popval", control, sub, names.get(control, control), quality["old"].at[control, sub],
                      row[sub], unit=unit, note=f"{references.get(control, '')} · {row['independence']}",
                      source="population_validation quality_summary.csv")

    # Trip purposes of the donor chains against MiD W1 (re-normalised over four purposes).
    purpose = {run: pd.read_csv(in_dir / f"popval_{run}" / "trip_coherence_purpose.csv").set_index("purpose")
               for run in RUNS}
    for name, row in purpose["new"].iterrows():
        facts.add("purpose", name, "share", name, purpose["old"].at[name, "realized_share"],
                  row["realized_share"], reference=row["target_share"], unit="Anteil",
                  note="reference: MiD 2023 W1", source="population_validation trip_coherence_purpose.csv")

    # Participation rates per Kreis in the final plans against the SrV targets.
    part = {run: pd.read_csv(in_dir / f"popval_{run}" / "participation_fit.csv", dtype={"ars5": str}).set_index(
        ["ars5", "purpose"]) for run in RUNS}
    for (kreis, purpose_name), row in part["new"].iterrows():
        facts.add("participation", kreis, purpose_name, KREIS_NAMES.get(kreis, kreis),
                  part["old"]["realised_rate"].get((kreis, purpose_name), np.nan), row["realised_rate"],
                  reference=row["target_rate"], unit="Anteil", note="reference: SrV 2023 target per Kreis",
                  source="population_validation participation_fit.csv")

    # Plan structure against SrV (segment "all").
    plan = {run: pd.read_csv(in_dir / f"analysis_{run}" / "plan_structure_vs_srv" / "headline.csv") for run in RUNS}
    plan = {run: frame[frame["segment"] == "all"].set_index("metric") for run, frame in plan.items()}
    plan_labels = {"mobility_rate": "Anteil mobiler Personen", "trips_per_person": "Wege je Person",
                   "trips_per_mobile_person": "Wege je mobiler Person",
                   "nonhome_acts_per_mobile": "Aktivitäten außer Haus je mobiler Person",
                   "tours_per_mobile": "Touren je mobiler Person", "share_trips_home_based": "Anteil Wege ab/zu Hause",
                   "share_mobile_odd_trip_count": "ungerade Wegezahl (mobile)",
                   "share_trips_followed_by_same_purpose": "Folgeweg mit gleichem Zweck"}
    for metric, label in plan_labels.items():
        if metric in plan["new"].index:
            facts.add("plan", metric, "", label, plan["old"].at[metric, "model"], plan["new"].at[metric, "model"],
                      reference=plan["new"].at[metric, "srv"], unit="", note="reference: SrV 2023 (n persons "
                      f"{int(plan['new'].at[metric, 'n_srv'])})", source="plan_structure_vs_srv headline.csv")

    # Commute distance against SrV, per Kreis and scope.
    commute = {run: pd.read_csv(in_dir / f"analysis_{run}" / "srv_distance_validation" / "commute_by_kreis.csv",
                                dtype={"code": str}).set_index(["code", "scope"]) for run in RUNS}
    bands = ["0_5", "5_10", "10_20", "20_30", "30_50", "50_100", "100_plus"]
    for (code, scope), row in commute["new"].iterrows():
        facts.add("commute_emd", code, scope, KREIS_NAMES.get(code, code),
                  commute["old"]["emd"].get((code, scope), np.nan), row["emd"], reference=row["noise_floor"],
                  unit="EMD", note=f"reference = noise floor; class new {row['classification']}; "
                  f"SrV persons {int(row['n_reference_persons'])}", source="srv_distance_validation commute_by_kreis.csv")
        for band in bands:
            facts.add("commute_band", f"{code}|{scope}", band, band.replace("_plus", "+").replace("_", "–") + " km",
                      commute["old"][f"model_share_{band}"].get((code, scope), np.nan), row[f"model_share_{band}"],
                      reference=row[f"target_share_{band}"], unit="Anteil", note="reference: SrV 2023",
                      source="srv_distance_validation commute_by_kreis.csv")

    # Departure times against SrV (earth mover's distance per segment, first trips).
    emd = {run: pd.read_csv(in_dir / f"analysis_{run}" / "departure_time_vs_srv" / "emd.csv") for run in RUNS}
    emd = {run: frame[frame["purpose"] == "all"].set_index(["segment", "position"]) for run, frame in emd.items()}
    for (segment, position), row in emd["new"].iterrows():
        facts.add("departure_emd", segment, position, segment,
                  emd["old"]["emd_vs_derounded"].get((segment, position), np.nan), row["emd_vs_derounded"],
                  unit="EMD", note=f"vs SrV de-rounded; {row['dimension']}", source="departure_time_vs_srv emd.csv")

    # Work trips of employed persons per Kreis against SrV.
    work = {run: pd.read_csv(in_dir / f"analysis_{run}" / "commute_day_state_phase_a" /
                             "work_participation_by_kreis.csv", dtype={"code": str}).set_index("code")
            for run in RUNS}
    for code, row in work["new"].iterrows():
        facts.add("work_trip", code, "share", KREIS_NAMES.get(code, code), work["old"]["share_work_trip"].get(code),
                  row["share_work_trip"], reference=row["srv_share_work_trip"], unit="Anteil",
                  note="employed persons with a work trip; reference SrV 2023",
                  source="commute_day_state_phase_a work_participation_by_kreis.csv")

    run_facts = pd.read_csv(in_dir / "run_facts.csv")
    for _, row in run_facts.iterrows():
        facts.add("run", row["item"], "", row["label"], row["old"], row["new"], unit=row["unit"],
                  note=row.get("note", ""), source=row["source"])
    return pd.DataFrame(facts)


CELL_GROUPS = ["household_total_100m", "household_size_100m", "age_sex_100m", "employed_grid_100m",
               "building_type_100m", "tenure_100m", "household_type_100m", "own_cars_1km", "own_bikes_1km",
               "household_type_1km"]


def build_cells(in_dir: Path) -> pd.DataFrame:
    """cells_1km.csv: one row per 1 km cell, old and new side by side (WAPE per group, counts, donors)."""
    frames = []
    for run in RUNS:
        cells = pd.read_csv(in_dir / f"zones_{run}" / "cells_1km.csv", dtype={"kreis": str})
        out = cells[["zone", "kreis", "x_km", "y_km"]].copy()
        for group in CELL_GROUPS:
            if f"{group}_target" in cells:
                target = cells[f"{group}_target"]
                out[f"{group}_wape_{run}"] = cells[f"{group}_abs_error"] / target.where(target > 0)
        out[f"households_target_{run}"] = cells["household_total_100m_target"]
        out[f"households_{run}"] = cells["household_total_100m_synthetic"]
        out[f"persons_target_{run}"] = cells["age_sex_100m_target"]
        out[f"persons_{run}"] = cells["age_sex_100m_synthetic"]
        out[f"donors_{run}"] = cells["n_distinct_donors"]
        out[f"kish_{run}"] = cells["kish_n_eff"]
        frames.append(out.set_index("zone"))
    old, new = frames
    merged = new.join(old.drop(columns=["kreis", "x_km", "y_km"]), how="outer")
    for column in ("kreis", "x_km", "y_km"):
        merged[column] = merged[column].fillna(old[column].reindex(merged.index))
    merged["kreis"] = merged["kreis"].astype(str).str.zfill(5)
    return merged.reset_index().round(5)


def build_plausibility(in_dir: Path) -> pd.DataFrame:
    """plausibility.csv: per control, Kreis (or region for household size) and category: reference share and
    the synthetic share of both runs."""
    frames = {}
    for run in RUNS:
        long = pd.read_csv(in_dir / f"popval_{run}" / "controls_long.csv", dtype={"geo_id": str})
        kreis = long[long["geography"] == "kreis"][["control", "geo_id", "category", "synthetic_pct", "target_pct"]]
        gem = long[long["geography"] == "gemeinde"].groupby(["control", "category"], as_index=False)[
            ["synthetic_count", "target_count"]].sum()
        gem["synthetic_pct"] = 100 * gem["synthetic_count"] / gem.groupby("control")["synthetic_count"].transform("sum")
        gem["target_pct"] = 100 * gem["target_count"] / gem.groupby("control")["target_count"].transform("sum")
        gem["geo_id"] = "region"
        frames[run] = pd.concat([kreis, gem[kreis.columns]]).set_index(["control", "geo_id", "category"])
    out = frames["new"].rename(columns={"synthetic_pct": "synthetic_pct_new", "target_pct": "reference_pct"})
    out["synthetic_pct_old"] = frames["old"]["synthetic_pct"].reindex(out.index)
    out = out.reset_index()
    out["kreis_name"] = out["geo_id"].map(KREIS_NAMES).fillna("Region (Summe der Gemeinden)")
    out["id"] = out["control"] + "|" + out["geo_id"] + "|" + out["category"].astype(str)
    return out[["id", "control", "geo_id", "kreis_name", "category", "reference_pct", "synthetic_pct_old",
                "synthetic_pct_new"]].round(4)


def _write_json(frame: pd.DataFrame, path: Path, name: str) -> None:
    """The same table as JSON {rows, meta} for the dashboard: numbers stay numbers, NaN becomes null, text columns
    stay text; meta carries this script's own source so a reader can check how the rows were made."""
    out = frame.copy()
    for column in ("id", "item", "sub", "zone", "kreis", "geo_id", "category", "control", "geography"):
        if column in out:
            out[column] = out[column].map(lambda v: v if pd.isna(v) else str(v))
    rows = json.loads(out.to_json(orient="records", force_ascii=False, double_precision=10))
    meta = [{"label": "build_dashboard_data.py", "value": Path(__file__).read_text(encoding="utf-8"),
             "type": "code", "lang": "python"},
            {"label": "Table", "value": f"{name} ({len(rows)} rows), same content as {name}.csv"}]
    path.write_text(json.dumps({"rows": rows, "meta": meta}, ensure_ascii=False), encoding="utf-8")


def main(in_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in (("controls", build_controls(in_dir)), ("facts", build_facts(in_dir)),
                        ("cells_1km", build_cells(in_dir)), ("plausibility", build_plausibility(in_dir))):
        frame.to_csv(out_dir / f"{name}.csv", index=False)
        _write_json(frame, out_dir / f"{name}.json", name)
        print(f"[build_dashboard_data] {name}.csv / {name}.json: {len(frame)} rows")
    # The activity grid (activity_grid.py) is passed through unchanged, as JSON for the heatmap.
    grid = pd.read_csv(in_dir / "grid" / "activity_grid_500m.csv")
    _write_json(grid, out_dir / "activity_grid.json", "activity_grid_500m")
    print(f"[build_dashboard_data] activity_grid.json: {len(grid)} rows")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
