"""The household-universe keys of ``data.census.filtered`` (issue #659, Task B4).

Three keys (meaning: ``braunschweig/popsim/stage/config_keys.py``):
``move_persons_of_household_less_cells``, ``household_less_cell_max_persons`` and
``family_controls``. The tests pin what a wrong wiring would break: the catalog of each
``family_controls`` mode, the seed contract over the two minor-child controls, the legacy
tables value for value, the move reaching ``build_control_totals`` through the batch run, the
1 km minor-child targets reaching the 1 km control table, and the fail-fast validation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from braunschweig.popsim import batch as popsim_batch
from braunschweig.popsim import control_spec, mid, minor_child_targets, seed, seed_contract, sources, stage
from braunschweig.popsim.sources.mid import MidSource
from braunschweig.popsim.stage import config_keys, controls_builder
from tests import popsim_control_build_fixture as fixture

NO_MINOR, ONE_ADULT, MULTI_ADULT = seed.HH_MINOR_CLASSES
FAMILY5_TARGETS = {
    f"{base}_ZENSUS100m_target" for base, _ in control_spec._TIER1_HH_TYPE_ENTRIES}
MINOR_TARGETS = {
    f"{minor_child_targets.ONE_ADULT_TARGET}_ZENSUS1km_target",
    f"{minor_child_targets.MULTI_ADULT_TARGET}_ZENSUS1km_target",
}
HH_TOTAL_1KM_TARGET = f"{control_spec.HH_TOTAL_CENSUS_COLUMN}_ZENSUS1km_target"


def _controls(family_controls: str, tiers=("tier0", "tier1")) -> pd.DataFrame:
    return controls_builder.build_controls_df(
        controls_source="catalog", seed="mid", tiers=tiers, family_controls=family_controls,
        importance_profile="optimized_2026_06_30")


@pytest.mark.parametrize("mode, expected_family5, expected_minor", [
    ("zensus_family5", FAMILY5_TARGETS, set()),
    ("none", set(), set()),
    ("minor_child_1km", set(), MINOR_TARGETS),
])
def test_family_controls_modes_select_the_controls(mode, expected_family5, expected_minor):
    controls = _controls(mode)
    targets = set(controls["target"])
    assert {t for t in targets if "Typ_priv_HH_Familie" in t} == expected_family5
    assert {t for t in targets if t.startswith("HH_MINOR_")} == expected_minor
    minor = controls[controls["target"].isin(MINOR_TARGETS)]
    assert set(minor["geography"]) <= {"ZENSUS1km"}
    assert set(minor["seed_table"]) <= {"households"}
    # Same importance treatment as the census family-type controls (group "hhtype").
    assert set(minor["importance"]) <= {control_spec.IMPORTANCE_PROFILES[
        "optimized_2026_06_30"]["hhtype"]}
    # The parquet load set holds the family-type columns only where a control reads them, and
    # never the minor-child targets (computed by the stage, not stored in the parquet).
    load = set(controls_builder.build_source_columns(
        controls_source="catalog", seed="mid", tiers=("tier0", "tier1"),
        family_controls=mode))
    family5_sources = {base for base, _ in control_spec._TIER1_HH_TYPE_ENTRIES}
    assert load & family5_sources == (family5_sources if mode == "zensus_family5" else set())
    assert not {t for t in load if t.startswith("HH_MINOR_")}


def _seed(minor_classes):
    households = pd.DataFrame({
        "H_ID": range(1, len(minor_classes) + 1), "H_GR": 2,
        "hh_type5": "paar_mit_kind", "hh_minor_class": list(minor_classes), "RegioStaR7": 73})
    persons = pd.DataFrame({"H_ID": [1], "P_ID": [1], "HP_ALTER": [40], "HP_SEX": [1]})
    return households, persons


def test_minor_child_controls_need_the_seed_column():
    households, persons = _seed(["one_adult"])
    with pytest.raises(ValueError, match="hh_minor_class"):
        seed_contract.check_seed_coverage(
            households.drop(columns="hh_minor_class"), persons,
            _controls("minor_child_1km", tiers=("tier0",)), HH_TOTAL_1KM_TARGET, mode="error",
            groups=[g for g in stage._seed_contract.CONTROL_GROUPS
                    if g.seed_table == "households"])


def test_seed_contract_accepts_the_minor_class():
    households, persons = _seed([ONE_ADULT, MULTI_ADULT, NO_MINOR, NO_MINOR])
    table = seed_contract.check_seed_coverage(
        households, persons, _controls("minor_child_1km", tiers=("tier0",)),
        HH_TOTAL_1KM_TARGET, mode="error",
        groups=[g for g in stage._seed_contract.CONTROL_GROUPS if g.seed_table == "households"])
    row = table.set_index("group").loc["household_minor"]
    # The two classes are not exhaustive: the households without a minor match no control.
    assert (row["n_universe"], row["n_uncovered"], row["n_multi_hit"]) == (4, 2, 0)


def test_legacy_keys_reproduce_the_control_tables():
    tables = fixture.build_control_tables(
        family_controls=config_keys.FAMILY_CONTROLS_DEFAULT, move_persons_max=None)
    for name in fixture.GOLDEN_NAMES:
        assert_frame_equal(tables[name], fixture.read_golden(name), check_exact=True)


class _ReportContext:
    """The two ``ExecuteContext`` methods the report writer uses."""

    def __init__(self, path):
        self._path = Path(path)
        self.info = {}

    def path(self):
        return str(self._path)

    def set_info(self, key, value):
        self.info[key] = value


def _fake_run_one(folder):
    xwalk = pd.read_csv(Path(folder) / "data" / "geo_cross_walk.csv", dtype=str)
    out_dir = Path(folder) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    xwalk.assign(H_ID=1).to_csv(out_dir / "final_expanded_household_ids.csv", index=False)
    return popsim_batch.BatchResult(str(folder), "succeeded", "ok", 0.0)


def _run_batches(tmp_path, cells, settings, family_controls="zensus_family5", stratify=False):
    prepared = stage._add_aggregated_control_columns(
        cells, fixture.CONTROL_SOURCE, fixture.SEED_NAME, fixture.CONTROL_TIERS,
        **fixture.AGGREGATE_KWARGS)
    controls_df, base_cols = stage._build_control_frame(
        fixture.CONTROL_SOURCE, None, fixture.SEED_NAME, fixture.CONTROL_TIERS, False, [],
        fixture.IMPORTANCE_PROFILE, fine_teen_age_bands=fixture.FINE_TEEN_AGE_BANDS,
        family_controls=family_controls)
    households, persons = _seed(["one_adult"])
    work_dir = tmp_path / "work"
    mid.run_popsim_mid(
        prepared, base_cols, controls_df, households, persons, work_dir=work_dir,
        settings_yaml="x: 1\n", logging_yaml="version: 1\n", max_cells=100,
        run_one=_fake_run_one, num_workers=1,
        **({"stratify_regiostar": True, "source": MidSource()} if stratify else {}),
        **settings.run_popsim_mid_kwargs(base_cols, fine_teen_age_bands=True))
    data = work_dir / "batch_000" / "data"
    return {geo: pd.read_csv(data / f"control_totals_{geo}.csv")
            for geo in ("ZENSUS100m", "ZENSUS1km")}, base_cols


# Both assemble_batch_folder call sites of run_popsim_mid (plain and RegioStaR-stratified)
# must pass the move on; the fixture cells and the seed are all RegioStaR7 73 (one stratum).
@pytest.mark.parametrize("stratify", [False, True], ids=["plain", "stratified"])
def test_move_option_reaches_build_control_totals(tmp_path, stratify):
    cells = fixture.make_cells()
    # The small cell (6 persons) becomes household-less: a donor under the 20-person bound.
    donor = cells["ZENSUS100m"] == "CRS3035RES100mN2900700E4401400"
    cells.loc[donor, control_spec.HH_TOTAL_CENSUS_COLUMN] = 0.0
    legacy, _ = _run_batches(tmp_path / "legacy", cells, stage.HouseholdUniverseSettings(
        False, 20.0, "zensus_family5"), stratify=stratify)
    settings = stage.HouseholdUniverseSettings(True, 20.0, "zensus_family5")
    moved, base_cols = _run_batches(tmp_path / "moved", cells, settings, stratify=stratify)

    age_cols = [f"{c}_ZENSUS100m" for c in base_cols if "_AGE_" in c]
    assert len(age_cols) == 22
    donor_row = moved["ZENSUS100m"]["ZENSUS100m"] == "CRS3035RES100mN2900700E4401400"
    assert legacy["ZENSUS100m"].loc[donor_row, age_cols].to_numpy().sum() > 0
    assert moved["ZENSUS100m"].loc[donor_row, age_cols].to_numpy().sum() == 0
    assert_frame_equal(moved["ZENSUS1km"], legacy["ZENSUS1km"], check_exact=True)

    context = _ReportContext(tmp_path)
    stage._write_household_universe_report(context, settings)
    report = json.loads((tmp_path / stage.CENSUS_FILTERED_REPORT_FILE).read_text("utf-8"))
    section = report["household_less_cells"]
    assert section["enabled"] is True and section["max_persons"] == 20.0
    assert section["totals"]["donor_cells"] == 1
    assert [b["batch"] for b in section["batches"]] == ["batch_000"]
    assert report["family_controls"]["mode"] == "zensus_family5"
    assert context.info["popsim_household_less_cells"] == section


def _write_family_table(tmp_path, squares, *, duplicate=False) -> Path:
    """A 1 km family table with the raw ('-') names: shares 0.1 (one adult), 0.2 (multi adult).

    One lone parent and two couples with a minor per 10 family-table households in every square.
    ``duplicate`` adds a second raw column that cleans to the household column's name.
    """
    raw = pd.DataFrame({minor_child_targets.SQUARE_ID_COLUMN: squares})
    for column in minor_child_targets.LONE_PARENT_SOURCES:
        raw[column] = 0.5
    for column in minor_child_targets.COUPLE_SOURCES:
        raw[column] = 2.0 / 3.0
    raw[minor_child_targets.FAMILY_TABLE_HOUSEHOLDS] = 10.0
    raw = raw.rename(columns=lambda c: c.replace("_1km_Gitter", "_1km-Gitter"))
    if duplicate:
        raw[minor_child_targets.FAMILY_TABLE_HOUSEHOLDS] = 99.0
    path = tmp_path / "grid_1km.parquet"
    raw.to_parquet(path, index=False)
    return path


def test_minor_child_mode_writes_the_1km_targets(tmp_path):
    cells = fixture.make_cells()
    path = _write_family_table(tmp_path, sorted(cells["ZENSUS1km"].unique()))

    with_targets, targets_report = stage._add_minor_child_targets(cells, path)
    settings = stage.HouseholdUniverseSettings(False, 20.0, "minor_child_1km")
    settings.targets_report = targets_report
    tables, _ = _run_batches(tmp_path, with_targets, settings, family_controls="minor_child_1km")

    km = tables["ZENSUS1km"].set_index("ZENSUS1km")
    one = km[f"{minor_child_targets.ONE_ADULT_TARGET}_ZENSUS1km"]
    multi = km[f"{minor_child_targets.MULTI_ADULT_TARGET}_ZENSUS1km"]
    # Square N2900000E4400000 holds 24.6 + 17.3 + 30.0 = 71.9 households (fixture).
    assert one["CRS3035RES1000mN2900000E4400000"] == 7      # round(7.19)
    assert multi["CRS3035RES1000mN2900000E4400000"] == 14   # round(14.38)
    assert targets_report["squares_on_kreis_fallback"] == 0

    stage._write_household_universe_report(_ReportContext(tmp_path), settings)
    report = json.loads((tmp_path / stage.CENSUS_FILTERED_REPORT_FILE).read_text("utf-8"))
    assert report["family_controls"] == {"mode": "minor_child_1km", "report": targets_report}
    assert report["household_less_cells"]["enabled"] is False


def test_minor_child_targets_in_production_order_from_the_catalog_load(tmp_path):
    """Load set -> parquet load -> aggregation -> targets, as in ``execute()`` (catalog source)."""
    fixture_cells = fixture.make_cells()
    cells_path = tmp_path / "grid_100m.parquet"
    fixture_cells.drop(columns=["ZENSUS1km"]).rename(columns={"ZENSUS100m": "GITTER_ID_100m"}).to_parquet(
        cells_path, index=False)
    family_path = _write_family_table(tmp_path, sorted(fixture_cells["ZENSUS1km"].unique()))
    base_cols = fixture.base_columns(family_controls="minor_child_1km")

    load_cols = stage._resolve_cell_load_columns(
        _ConfigureContext({}), fixture.CONTROL_SOURCE, fixture.SEED_NAME, fixture.CONTROL_TIERS,
        base_cols, False,
        cells_path, [], fine_teen_age_bands=fixture.FINE_TEEN_AGE_BANDS,
        family_controls="minor_child_1km")
    cells = mid.load_control_cells(cells_path, load_cols)
    cells = stage._add_aggregated_control_columns(
        cells, fixture.CONTROL_SOURCE, fixture.SEED_NAME, fixture.CONTROL_TIERS,
        family_controls="minor_child_1km", **fixture.AGGREGATE_KWARGS)
    out, report = stage._add_minor_child_targets(cells, family_path)

    households = out.groupby("ZENSUS1km")[control_spec.HH_TOTAL_CENSUS_COLUMN].sum()
    one = out.groupby("ZENSUS1km")[minor_child_targets.ONE_ADULT_TARGET].sum()
    multi = out.groupby("ZENSUS1km")[minor_child_targets.MULTI_ADULT_TARGET].sum()
    assert households["CRS3035RES1000mN2900000E4400000"] == pytest.approx(71.9)
    assert one.to_numpy() == pytest.approx(0.1 * households.to_numpy())
    assert multi.to_numpy() == pytest.approx(0.2 * households.to_numpy())
    assert report["squares_on_kreis_fallback"] == 0
    assert report["cells_with_missing_households"] == 0


def test_minor_child_targets_refuse_two_raw_columns_with_one_clean_name(tmp_path):
    cells = fixture.make_cells()
    path = _write_family_table(tmp_path, sorted(cells["ZENSUS1km"].unique()), duplicate=True)

    with pytest.raises(ValueError, match="clean to the same column name"):
        stage._add_minor_child_targets(cells, path)


class _ConfigureContext:
    """``configure()`` double: answers the overridden keys, else the declared default."""

    def __init__(self, overrides):
        self.overrides = overrides

    def config(self, key, default=None, **_):
        return self.overrides.get(key, default)

    def stage(self, *_, **__):
        return None


@pytest.mark.parametrize("key, value", [
    (config_keys.KEY_FAMILY_CONTROLS, "zensus_family_5"),
    (config_keys.KEY_MOVE_PERSONS_OF_HOUSEHOLD_LESS_CELLS, "yes"),
    (config_keys.KEY_HOUSEHOLD_LESS_CELL_MAX_PERSONS, 0.0),
    (config_keys.KEY_HOUSEHOLD_LESS_CELL_MAX_PERSONS, float("inf")),
])
def test_invalid_family_controls_value_raises(key, value):
    with pytest.raises(ValueError, match=key.replace(".", r"\.")):
        stage.configure(_ConfigureContext({key: value}))


def test_minor_child_controls_refuse_the_entd_source():
    # The ENTD seed has no hh_minor_class: fail at DAG-build time, not after the seed build.
    with pytest.raises(ValueError, match="is 'minor_child_1km' but .* is 'entd'"):
        stage.configure(_ConfigureContext({
            config_keys.KEY_FAMILY_CONTROLS: "minor_child_1km", config_keys.KEY_SOURCE: "entd"}))


def test_entd_source_with_the_legacy_family_controls_is_accepted():
    # Positive control of the refusal above: only the minor-child mode needs the MiD seed.
    stage._require_family_controls_source("zensus_family5", "entd")
    stage._require_family_controls_source("none", "entd")
    stage._require_family_controls_source("minor_child_1km", "mid")


# --- the legacy seed and batch signature are those of the commit before #659 Part B ---------

# The base commit before #659 Part B (bc5e5b54) built the seed without hh_minor_class and passed
# no household-universe payload to the batch signature. A legacy run must keep that signature, or
# it purges its completed batches. The reference is computed in the same process: a hash pinned
# on one platform differs on another (the signature hashes the seed frames, whose integer dtype is
# platform-dependent), which turned the Linux CI red while Windows passed.


def _legacy_signature(settings):
    cols = sources.get_source("mid").seed_columns()
    households = pd.DataFrame({
        cols.household_id: ["h1", "h2", "h3"],
        cols.household_weight: [1.0, 2.0, 0.5],
        "H_GR": [1, 3, 2], "H_MIETE": [1, 2, 1], "haustyp": [2, 1, 4],
        "RegioStaR7": [71, 73, 73],
    })
    persons = pd.DataFrame({
        cols.person_household_id: ["h1", "h2", "h2", "h2", "h3", "h3"],
        cols.person_id: ["p1", "p2", "p3", "p4", "p5", "p6"],
        cols.person_weight: [1.0, 2.0, 2.0, 2.0, 0.5, 0.5],
        cols.age: [34, 41, 39, 9, 45, 16],
        cols.sex: [2, 1, 2, 1, 2, 1],
    })
    raw_seed_hh, seed_p = mid.project_completed_seed(households, persons, cols)
    seed_hh = settings.seed_households_for_run(raw_seed_hh)

    def signature_of(seed_households, payload):
        return stage.compute_batch_config_signature(
            controls_df=pd.DataFrame({"target": ["total_households"], "importance": [1000]}),
            settings_text="geographies: [ZENSUS100m]\n", max_cells=1500, stratify_regiostar=False,
            source_name="mid", employment_grid_on=False, kreis_controls_map=None,
            seed_day_filter=None, seed_households=seed_households, seed_persons=seed_p,
            kreis_table=None, populationsim_version="0.10.0+bs.1", populationsim_commit="c0ffee",
            populationsim_lock_hash="lockhash", settings_overrides={"INTEGERIZER_DRAW": "off"},
            integerizer_seed_base=None, household_universes=payload)

    # What the base commit computed: no hh_minor_class column, no household-universe payload.
    base_commit = signature_of(raw_seed_hh.drop(columns=["hh_minor_class"], errors="ignore"), None)
    return seed_hh, signature_of(seed_hh, settings.signature_payload()), base_commit


@pytest.mark.parametrize("keys, legacy", [
    ((False, 20.0, "zensus_family5"), True),
    # The bound alone, with the move off, leaves the tables as they were: still legacy.
    ((False, 35.0, "zensus_family5"), True),
    ((True, 20.0, "zensus_family5"), False),
    ((False, 20.0, "none"), False),
    ((False, 20.0, "minor_child_1km"), False),
])
def test_legacy_keys_keep_the_seed_and_signature_of_the_base_commit(keys, legacy):
    seed_hh, signature, base_commit = _legacy_signature(stage.HouseholdUniverseSettings(*keys))
    # Only the minor-child controls read hh_minor_class; every other mode seeds without it.
    assert ("hh_minor_class" in seed_hh.columns) == (keys[2] == "minor_child_1km")
    assert (signature == base_commit) == legacy


@pytest.mark.parametrize("family", [
    control_spec.FAMILY_CONTROLS_ZENSUS_FAMILY5, control_spec.FAMILY_CONTROLS_NONE,
    control_spec.FAMILY_CONTROLS_MINOR_CHILD_1KM])
def test_placement_income_signatures_cover_the_enforced_family_controls(family):
    # The income placement only keeps every control aggregate when its donor signatures are
    # built from the controls PopulationSim enforced; in every family mode that is the catalog
    # of the same mode.
    tiers = ("tier0", "tier1", "tier2")
    placement = stage._placement_income_catalog(tiers, False, (), True, family)
    enforced = control_spec.full_catalog(include_tiers=tiers, fine_teen_age_bands=True,
                                         family_controls=family)
    assert [c.name for c in placement] == [c.name for c in enforced]
    names = " ".join(c.name for c in placement)
    assert ("HH_MINOR_ONE_ADULT" in names) == (family == control_spec.FAMILY_CONTROLS_MINOR_CHILD_1KM)
    assert ("Typ_priv_HH_Familie_100m" in names) == (family == control_spec.FAMILY_CONTROLS_ZENSUS_FAMILY5)
