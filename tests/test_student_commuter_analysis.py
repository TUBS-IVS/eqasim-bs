import os

import pandas as pd
from braunschweig.analysis.simwrapper import student_commuters as sc


def test_student_od_aggregates_and_sums_to_count():
    persons = pd.DataFrame({
        "orig_ars5": ["09999", "09999", "16077"],
        "dest_commune": ["03101", "03101", "03158"]})
    od = sc.student_od(persons)
    assert od.set_index(["from_ars5", "to_commune"]).loc[("09999", "03101"), "value"] == 2
    assert od["value"].sum() == len(persons)


def test_student_od_origins_outside_zgb():
    persons = pd.DataFrame({"orig_ars5": ["09999"], "dest_commune": ["03101"]})
    od = sc.student_od(persons)
    assert not od["from_ars5"].str.startswith("031").any()  # no ZGB-BS origin


def test_write_outputs_empty_input_writes_no_files(tmp_path):
    persons = pd.DataFrame({"orig_ars5": [], "dest_commune": []})
    distances = pd.Series([], dtype=float)
    sc.write_outputs(persons, distances, str(tmp_path))
    written = set(os.listdir(tmp_path)) if tmp_path.exists() else set()
    assert written.isdisjoint({
        "student_commuter_od.csv",
        "student_commuter_top_relations.csv",
        "student_commute_distance.csv",
    })


def test_write_outputs_unnamed_distance_series_yields_band_column(tmp_path):
    persons = pd.DataFrame({
        "orig_ars5": ["09999", "09999", "16077"],
        "dest_commune": ["03101", "03101", "03158"]})
    # Deliberately unnamed Series: reset_index() would otherwise call the
    # grouping column "index", not "band" -- this is the regression case for
    # the distance-band rename bug.
    distances = pd.Series([3.0, 7.0, 42.0])
    assert distances.name is None
    sc.write_outputs(persons, distances, str(tmp_path))

    od_path = tmp_path / "student_commuter_od.csv"
    top_path = tmp_path / "student_commuter_top_relations.csv"
    dist_path = tmp_path / "student_commute_distance.csv"
    assert od_path.exists()
    assert top_path.exists()
    assert dist_path.exists()

    dist_df = pd.read_csv(dist_path)
    assert list(dist_df.columns) == ["band", "count", "mean_km"]

    top_df = pd.read_csv(top_path)
    assert list(top_df.columns) == ["from", "to", "value"]

    od_df = pd.read_csv(od_path)
    assert list(od_df.columns) == ["from_ars5", "to_commune", "value"]
