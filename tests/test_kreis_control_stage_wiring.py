"""Task 4 wiring tests: generic KREIS-attribute-control catalog assembly + the
economic_status target swap.

Pure catalog/loader checks (no PopulationSim run, no MiD microdata): they confirm that
- the generic ``attribute_kreis_controls`` factory renders the count-style predicates,
- the full catalog / ``build_controls_df`` include the active KREIS controls only when
  requested (OFF byte-identical),
- and the ``economic_status`` registry entry now sources its per-Kreis target from the
  committed blended table ``target2026_economic_status_by_kreis.csv`` (not the old H4 CSV).

The committed blended target is read to recompute the expected count table, proving the
swap took effect (the shares differ from the old H4 percentages).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

from braunschweig.popsim import control_spec as cs  # noqa: E402
from braunschweig.popsim.kreis_attribute_control import (  # noqa: E402
    REGISTRY,
    control_columns,
    load_kreis_target,
    attribute_kreis_count_table,
)


def _default_active_kreis_entry_names() -> set:
    """The REGISTRY entry names ACTIVE under the declared default config (all toggles on).

    The per-toggle tests below assert "switching this entry off drops EXACTLY this entry",
    which is the behaviour under test; they derive the expected remainder from the registry
    instead of re-listing it, so adding a control (e.g. pt_ticket_group, issue #321) does not
    require touching every one of them. The full default-on set stays pinned explicitly in
    tests/test_popsim_seed_kreis_columns.py::test_all_kreis_entries_default_on, so a
    silently vanishing entry is still caught.

    ``pt_ticket_group`` is excluded because it is not merely registered but SUBSTITUTED:
    with ``pt_ticket_never_group`` on (the default, issue #329) the four-group
    ``pt_ticket_group4`` entry replaces it -- see
    ``source_resolution.active_kreis_entries``.
    """
    from braunschweig.popsim import kreis_attribute_control as kac
    return {c.name for c in kac.REGISTRY} - {"pt_ticket_group"}


def _entry(name):
    return next(c for c in REGISTRY if c.name == name)


# --- Step 1: generic factory renders the count-style predicates ---


def test_attribute_controls_render_range_predicate_for_cars():
    controls = cs.attribute_kreis_controls([_entry("number_of_cars")])
    exprs = {c.name: c.expression_for("mid") for c in controls}
    assert exprs["number_of_cars_3plus"] == "(households.number_of_cars >= 3)"
    assert exprs["number_of_cars_0"] == "(households.number_of_cars == 0)"
    # ENTD cannot express donor columns -> dropped
    assert all(c.expression_for("entd") is None for c in controls)


def test_attribute_controls_render_person_predicate_for_trip_class():
    # trip_class is the first PERSON-level entry: its control expressions must reference
    # the PERSONS seed table (persons.trip_class == k) for the four int-coded classes.
    controls = cs.attribute_kreis_controls([_entry("trip_class")])
    exprs = {c.name: c.expression_for("mid") for c in controls}
    assert exprs["trip_class_0"] == "(persons.trip_class == 0)"
    assert exprs["trip_class_1_2"] == "(persons.trip_class == 1)"
    assert exprs["trip_class_3_4"] == "(persons.trip_class == 2)"
    assert exprs["trip_class_5plus"] == "(persons.trip_class == 3)"
    # The controls sit on the persons seed table + KREIS geography.
    assert all(c.seed_table == "persons" for c in controls)
    assert all(c.geography == "KREIS" for c in controls)
    # ENTD cannot express the MiD donor diary column -> dropped.
    assert all(c.expression_for("entd") is None for c in controls)


def test_off_path_builds_no_kreis_controls():
    # all toggles off -> catalog has no economic_status_* / number_of_cars_* controls
    controls = cs.full_catalog(("tier0",), include_status_kreis=False)
    names = {c.name for c in controls}
    assert not any(
        n.startswith("economic_status_") or n.startswith("number_of_cars_") for n in names
    )


# --- Step 1: economic_status target swap ---


def test_economic_status_entry_now_points_at_blended_target():
    econ = _entry("economic_status")
    assert econ.target_csv_relpath == "braunschweig/targets/target2026_economic_status_by_kreis.csv"


# --- Additional wiring assertions ---


def test_full_catalog_renders_all_active_kreis_control_names():
    # kreis_control_names is the generalised knob; passing cars + bikes renders both.
    controls = cs.full_catalog(
        ("tier0",), kreis_control_names=("number_of_cars", "number_of_bicycles")
    )
    names = {c.name for c in controls}
    assert set(control_columns(_entry("number_of_cars"))) <= names
    assert set(control_columns(_entry("number_of_bicycles"))) <= names
    # economic_status was NOT requested -> absent.
    assert not any(n.startswith("economic_status_") for n in names)


def test_full_catalog_status_kreis_alias_maps_to_economic_status():
    # include_status_kreis is kept as a backward-compat alias for ("economic_status",).
    by_alias = cs.full_catalog(("tier0",), include_status_kreis=True)
    by_name = cs.full_catalog(("tier0",), kreis_control_names=("economic_status",))
    assert [c.name for c in by_alias] == [c.name for c in by_name]
    econ_cols = set(control_columns(_entry("economic_status")))
    assert econ_cols <= {c.name for c in by_alias}


def test_build_controls_df_off_path_has_no_kreis_controls():
    # OFF byte-identical: no kreis_control_names, status_kreis False -> no KREIS controls
    # in the rendered controls.csv frame.
    from braunschweig.popsim.stage import build_controls_df

    off = build_controls_df(controls_source="catalog", seed="mid", tiers=("tier0",))
    fields = set(off["control_field"])
    assert not any(
        f.startswith("economic_status_") or f.startswith("number_of_cars_")
        or f.startswith("number_of_bicycles_") or f.startswith("has_ebike_")
        or f.startswith("trip_class_")
        for f in fields
    )


def test_build_controls_df_renders_requested_kreis_controls():
    from braunschweig.popsim.stage import build_controls_df

    on = build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0",),
        kreis_control_names=("number_of_cars",),
    )
    fields = set(on["control_field"])
    expected = {f"{c}_KREIS" for c in control_columns(_entry("number_of_cars"))}
    assert expected <= fields
    rows = on[on["control_field"].isin(expected)]
    assert (rows["geography"] == "KREIS").all()
    assert (rows["seed_table"] == "households").all()


def test_build_controls_df_renders_trip_class_person_controls():
    # The person-level trip_class entry renders four KREIS controls on the persons table.
    from braunschweig.popsim.stage import build_controls_df

    on = build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0",),
        kreis_control_names=("trip_class",),
    )
    fields = set(on["control_field"])
    expected = {f"{c}_KREIS" for c in control_columns(_entry("trip_class"))}
    assert expected <= fields
    rows = on[on["control_field"].isin(expected)]
    assert (rows["geography"] == "KREIS").all()
    assert (rows["seed_table"] == "persons").all()


def test_build_controls_df_status_kreis_alias_still_renders_economic_status():
    # The status_kreis=True alias must keep working byte-identically (existing callers).
    from braunschweig.popsim.stage import build_controls_df

    by_alias = build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0",), status_kreis=True
    )
    by_name = build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0",),
        kreis_control_names=("economic_status",),
    )
    assert list(by_alias["control_field"]) == list(by_name["control_field"])
    expected = {f"{c}_KREIS" for c in control_columns(_entry("economic_status"))}
    assert expected <= set(by_alias["control_field"])


def test_build_controls_df_kreis_control_rejected_for_csv_source(tmp_path):
    # The hand-edited CSV source cannot express KREIS attribute controls -> fail-fast.
    from braunschweig.popsim.stage import build_controls_df

    csv = tmp_path / "controls.csv"
    csv.write_text("target;geography;seed_table;importance;control_field;expression\n")
    with pytest.raises(ValueError):
        build_controls_df(
            controls_source="csv", controls_path=str(csv), seed="mid",
            kreis_control_names=("number_of_cars",),
        )


# --- active_kreis_entries toggle resolution ---


class _FakeContext:
    """Minimal synpp ExecuteContext stand-in.

    Mirrors the REAL execute-time contract: ``config(key)`` takes NO default argument
    (synpp's ``ExecuteContext.config`` raises ``TypeError`` on a positional default --
    the exact bug the 2026-07-08 server smoke caught). Values are resolved as configure()
    declares them: an explicit value wins, otherwise the per-entry default from
    ``stage._KREIS_CONTROL_DEFAULT`` (the same map configure() declares).
    """

    def __init__(self, values):
        self._values = values

    def config(self, key):
        if key in self._values:
            return self._values[key]
        from braunschweig.popsim import stage
        for name, toggle_key in stage._KREIS_CONTROL_TOGGLE_KEY.items():
            if key == toggle_key:
                return stage._KREIS_CONTROL_DEFAULT[name]
        raise KeyError(f"_FakeContext: no value or declared default for config key {key!r}")


def test_active_kreis_entries_all_default_on_for_mid():
    from braunschweig.popsim import stage

    # Empty config -> all eleven entries default "on" (project rule: new features default
    # on), in REGISTRY order. has_ebike's source column (H_ANZPED) was server-verified
    # 2026-07-08; trip_class (first person-level entry) is the 2026-07-08 follow-on;
    # employment_status (second person-level entry, 14+ universe) is feature #172 task 4;
    # work_participation / leisure_participation / education_participation (third,
    # fourth, fifth person-level entries) are feature #224 tasks 4-5; pt_ticket_group
    # (sixth person-level entry, 14+ universe) is issue #321 -- and appears here as its
    # four-group refinement pt_ticket_group4, which REPLACES it while
    # pt_ticket_never_group is on (the default, issue #329); escort_participation
    # (seventh person-level entry) is issue #227.
    active = stage.active_kreis_entries(_FakeContext({}), "mid")
    assert [c.name for c in active] == [
        "economic_status", "number_of_cars", "number_of_bicycles", "has_ebike", "trip_class",
        "employment_status", "pt_ticket_group4", "work_participation", "leisure_participation",
        "education_participation", "escort_participation",
    ]


def test_active_kreis_entries_empty_for_non_mid_source():
    from braunschweig.popsim import stage

    # KREIS attribute controls are MiD-only (no ENTD pendant) -> empty for any other source.
    assert stage.active_kreis_entries(_FakeContext({}), "entd") == []


def test_active_kreis_entries_all_off_is_empty():
    from braunschweig.popsim import stage

    off = {
        stage.KEY_STATUS_KREIS_CONTROL: "off",
        stage.KEY_CARS_KREIS_CONTROL: "off",
        stage.KEY_BIKES_KREIS_CONTROL: "off",
        stage.KEY_EBIKE_KREIS_CONTROL: "off",
        stage.KEY_TRIPS_KREIS_CONTROL: "off",
        stage.KEY_EMPLOYMENT_STATUS_KREIS_CONTROL: "off",
        stage.KEY_PT_TICKET_KREIS_CONTROL: "off",
        # The four-group refinement must be off too: "on" with the base control off is a
        # config error (fail-fast), not an empty control set (issue #329).
        stage.KEY_PT_TICKET_NEVER_GROUP: "off",
        stage.KEY_WORK_PARTICIPATION_CONTROL: "off",
        stage.KEY_LEISURE_PARTICIPATION_CONTROL: "off",
        stage.KEY_EDUCATION_PARTICIPATION_CONTROL: "off",
        stage.KEY_ESCORT_PARTICIPATION_CONTROL: "off",
    }
    assert stage.active_kreis_entries(_FakeContext(off), "mid") == []


def test_active_kreis_entries_individual_toggle():
    from braunschweig.popsim import stage

    # Turning off only number_of_cars keeps every other entry active (all default "on";
    # see test_active_kreis_entries_has_ebike_can_be_turned_off).
    active = stage.active_kreis_entries(
        _FakeContext({stage.KEY_CARS_KREIS_CONTROL: "off"}), "mid"
    )
    names = [c.name for c in active]
    assert "number_of_cars" not in names
    assert set(names) == _default_active_kreis_entry_names() - {"number_of_cars"}


def test_active_kreis_entries_has_ebike_can_be_turned_off():
    from braunschweig.popsim import stage

    # An explicit "off" always wins over the (now "on") per-entry default.
    active = stage.active_kreis_entries(
        _FakeContext({stage.KEY_EBIKE_KREIS_CONTROL: "off"}), "mid"
    )
    names = {c.name for c in active}
    assert "has_ebike" not in names


def test_active_kreis_entries_trip_class_can_be_turned_off():
    from braunschweig.popsim import stage

    # An explicit "off" for the person-level trip_class entry drops only that entry.
    active = stage.active_kreis_entries(
        _FakeContext({stage.KEY_TRIPS_KREIS_CONTROL: "off"}), "mid"
    )
    names = {c.name for c in active}
    assert "trip_class" not in names
    assert names == _default_active_kreis_entry_names() - {"trip_class"}


def test_active_kreis_entries_employment_status_can_be_turned_off():
    from braunschweig.popsim import stage

    # An explicit "off" for the second person-level entry (employment_status, feature
    # #172 task 4) drops only that entry.
    active = stage.active_kreis_entries(
        _FakeContext({stage.KEY_EMPLOYMENT_STATUS_KREIS_CONTROL: "off"}), "mid"
    )
    names = {c.name for c in active}
    assert "employment_status" not in names
    assert names == _default_active_kreis_entry_names() - {"employment_status"}


def test_active_kreis_entries_work_participation_can_be_turned_off():
    from braunschweig.popsim import stage

    # An explicit "off" for the third person-level entry (work_participation, feature
    # #224 task 4) drops only that entry.
    active = stage.active_kreis_entries(
        _FakeContext({stage.KEY_WORK_PARTICIPATION_CONTROL: "off"}), "mid"
    )
    names = {c.name for c in active}
    assert "work_participation" not in names
    assert names == _default_active_kreis_entry_names() - {"work_participation"}


def test_active_kreis_entries_leisure_participation_can_be_turned_off():
    from braunschweig.popsim import stage

    # An explicit "off" for the fourth person-level entry (leisure_participation,
    # feature #224 task 5) drops only that entry.
    active = stage.active_kreis_entries(
        _FakeContext({stage.KEY_LEISURE_PARTICIPATION_CONTROL: "off"}), "mid"
    )
    names = {c.name for c in active}
    assert "leisure_participation" not in names
    assert names == _default_active_kreis_entry_names() - {"leisure_participation"}


def test_active_kreis_entries_education_participation_can_be_turned_off():
    from braunschweig.popsim import stage

    # An explicit "off" for the fifth person-level entry (education_participation,
    # feature #224 task 5) drops only that entry.
    active = stage.active_kreis_entries(
        _FakeContext({stage.KEY_EDUCATION_PARTICIPATION_CONTROL: "off"}), "mid"
    )
    names = {c.name for c in active}
    assert "education_participation" not in names
    assert names == _default_active_kreis_entry_names() - {"education_participation"}


# --- issue #329: the four-group PT control REPLACES the three-group one ---


def test_pt_ticket_group4_replaces_three_group_entry():
    from braunschweig.popsim import stage

    # All toggles "on" (the declared defaults) -> pt_ticket_group4 active, the three-group
    # pt_ticket_group NOT: both steer the SAME marginal at different resolutions, so
    # running them together would double-constrain the flatrate mass.
    names = [e.name for e in stage.active_kreis_entries(_FakeContext({}), "mid")]
    assert "pt_ticket_group4" in names
    assert "pt_ticket_group" not in names


def test_pt_ticket_group4_requires_base_control():
    from braunschweig.popsim import stage

    # never_group "on" + base pt_ticket_kreis_control "off" -> hard error (fail-fast): the
    # four-group control REFINES the three-group one; silently activating it while the base
    # control is off would hide a config contradiction.
    ctx = _FakeContext({stage.KEY_PT_TICKET_KREIS_CONTROL: "off"})
    with pytest.raises(ValueError, match="pt_ticket_never_group"):
        stage.active_kreis_entries(ctx, "mid")


def test_pt_ticket_never_group_off_restores_three_groups():
    from braunschweig.popsim import stage

    # "off" restores exactly the pre-#329 behaviour: the three-group entry, no group4.
    ctx = _FakeContext({stage.KEY_PT_TICKET_NEVER_GROUP: "off"})
    names = [e.name for e in stage.active_kreis_entries(ctx, "mid")]
    assert "pt_ticket_group" in names
    assert "pt_ticket_group4" not in names


# --- OFF byte-identical: controls.csv unchanged from the pre-task default ---


def test_off_controls_csv_byte_identical_to_pre_task_default():
    # Pre-task default (no kreis controls) == status_kreis=False == no kreis_control_names.
    from braunschweig.popsim.stage import build_controls_df

    a = build_controls_df(controls_source="catalog", seed="mid", tiers=("tier0", "tier1"))
    b = build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0", "tier1"), status_kreis=False
    )
    c = build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0", "tier1"), kreis_control_names=()
    )
    assert a.to_csv(index=False) == b.to_csv(index=False) == c.to_csv(index=False)


# --- Pin-recompute: the switched economic_status count table matches the BLENDED shares ---

_DATA_PATH = REPO / "eqasim-data" / "data"
_BLENDED_CSV = _DATA_PATH / "braunschweig" / "targets" / "target2026_economic_status_by_kreis.csv"


@pytest.mark.skipif(not _BLENDED_CSV.exists(), reason="committed blended target CSV not present")
def test_economic_status_count_table_uses_blended_not_h4_shares():
    econ = _entry("economic_status")
    # The committed blended target stores shares rounded to 4 decimals (a row can sum to
    # 0.9999 / 1.0001), so load it with the same 1e-3 tolerance the stage uses; the default
    # 1e-6 would reject the committed rounding (see stage.py _kac_share_tol).
    target = load_kreis_target(_DATA_PATH, econ, expected_ars5=("03101",), share_tolerance=1e-3)
    # A clean 10000-household total on Braunschweig (03101) so the exact fractions render
    # to integer counts without rounding ambiguity.
    tbl = attribute_kreis_count_table(econ, target, {"03101": 10000}, prior_n=0.0)
    cols = list(control_columns(econ))
    row = tbl[tbl["ARS_kreis"] == "03101"][cols].to_numpy().ravel().tolist()
    # BLENDED 03101 shares (target2026_economic_status_by_kreis.csv):
    #   very_low=0.1213 low=0.1129 medium=0.2943 high=0.3198 very_high=0.1517  (sum = 1.0)
    # x 10000 households -> the expected integer partition below. These numbers are the
    # committed blended target, NOT the old MiD H4 percentages (0.10/0.08/0.30/0.36/0.16).
    assert row == [1213, 1129, 2943, 3198, 1517]
    # sanity: NOT the old H4 partition (would be 1000/800/3000/3600/1600).
    assert row != [1000, 800, 3000, 3600, 1600]
    assert sum(row) == 10000
