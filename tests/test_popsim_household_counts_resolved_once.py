"""Household count attributes are resolved ONCE per donor household (audit P3-F1 / P3-F4).

MiD item-nonresponse codes (99) of H_ANZAUTO / anzpedrad / H_ANZPED used to be imputed
twice with different RNG streams: on the seed (``kreis_seed_rng``) that PopulationSim
balances the Kreis controls on, and again in ``assembly.map_mid_person_attributes`` (default
``RandomState(0)``) before the values were attached to the cloned households. And
``has_ebike`` / ``number_of_bicycles`` were imputed independently, so a household could
end with an e-bike and zero bicycles.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pandas as pd

from braunschweig.popsim import assembly, attributes, expand, mid, sources
from braunschweig.popsim import stage as popsim_stage
from braunschweig.popsim.kreis_attribute_control import REGISTRY


def _entry(name):
    return next(c for c in REGISTRY if c.name == name)


def _donor_households(n: int = 60) -> pd.DataFrame:
    """Donor households with 99 in all three raw columns, in two hhgr_gr groups.

    Valid rows are consistent (anzpedrad >= H_ANZPED, as MiD builds it); the missing
    codes sit in H_ANZAUTO, anzpedrad, H_ANZPED, jointly and separately.
    """
    rng = np.random.RandomState(5)
    cols = sources.get_source("mid").seed_columns()
    ped = rng.randint(0, 3, size=n)
    rad = rng.randint(0, 4, size=n)
    bikes = np.minimum(ped + rad, 10)
    cars = rng.randint(0, 4, size=n)
    cars[::5] = 99
    bikes[::4] = 99
    ped[::3] = 99
    return pd.DataFrame({
        cols.household_id: [f"h{i}" for i in range(n)],
        cols.household_weight: 1.0,
        "H_GR": 2, "H_MIETE": 1, "haustyp": 1,
        "RegioStaR7": 73,
        "hhgr_gr": [1 + (i % 2) for i in range(n)],
        "oek_status": 3, "hheink_gr1": 4,
        "H_ANZAUTO": cars, "H_ANZRAD": rad, "anzpedrad": bikes, "H_ANZPED": ped,
    })


def test_bicycles_and_ebike_are_consistent_after_joint_imputation():
    hh = _donor_households()
    assert (hh["anzpedrad"] == 99).any() and (hh["H_ANZPED"] == 99).any()
    out = attributes.map_household_bicycles_and_ebike(hh, rng=np.random.RandomState(1))
    assert not ((out["has_ebike"] == 1) & (out["number_of_bicycles"] == 0)).any()
    ok_b = hh["anzpedrad"] != 99
    assert (out.loc[ok_b, "number_of_bicycles"] == hh.loc[ok_b, "anzpedrad"]).all()
    ok_p = hh["H_ANZPED"] != 99
    assert (out.loc[ok_p, "has_ebike"] == (hh.loc[ok_p, "H_ANZPED"] >= 1).astype(int)).all()
    assert out["number_of_bicycles"].between(0, 10).all()


def test_joint_imputation_is_consistent_for_many_seeds():
    hh = _donor_households(200)
    for seed in range(10):
        out = attributes.map_household_bicycles_and_ebike(hh, rng=np.random.RandomState(seed))
        assert not ((out["has_ebike"] == 1) & (out["number_of_bicycles"] == 0)).any()


def test_joint_imputation_logs_primary_imputed_and_fallback_rates(caplog):
    hh = _donor_households()
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.attributes"):
        attributes.map_household_bicycles_and_ebike(hh, rng=np.random.RandomState(1))
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "primary" in text and "imputed" in text and "fallback" in text


class _FakeContext:
    """Minimal synpp context: serves the completed_donor stage result, records set_info."""

    def __init__(self, donor):
        self._donor = donor
        self.info = {}

    def stage(self, name):
        assert name == "completed_donor"
        return self._donor

    def set_info(self, key, value):
        self.info[key] = value


_COUNT_ENTRIES = ("number_of_cars", "number_of_bicycles", "has_ebike")


def _seed_and_assembled_clones(donors, mid_persons_extra=None, entries=_COUNT_ENTRIES):
    """Run the stage's seed build, then assemble two clones per donor from ITS donor tables.

    Uses only entry points that exist on origin/main (``_build_populationsim_seed``,
    ``expand_to_persons``, ``assembly.map_mid_person_attributes``), so the test is red on the
    pre-fix code for the real defect, not for a missing new function.
    """
    cols = sources.get_source("mid").seed_columns()
    ids = donors[cols.household_id].tolist()
    mid_persons = pd.DataFrame({
        "H_ID": ids, "P_ID": [1] * len(ids), "HP_ALTER": 40, "HP_SEX": 1, "P_TAET": 1,
        "P_FSCHEIN": 1, "P_FKARTE": 3, "P_BKAT": 1,
    })
    if mid_persons_extra is not None:
        mid_persons = mid_persons_extra(mid_persons)
    seed_persons_in = pd.DataFrame({
        cols.person_household_id: mid_persons["H_ID"], cols.person_id: mid_persons["P_ID"],
        cols.person_weight: 1.0, cols.age: 40, cols.sex: 1,
        "anzwege1": 2, "alter_gr1": 5,
        **{c: mid_persons[c] for c in ("P_TAET", "P_FSCHEIN", "P_FKARTE", "P_BKAT")},
    })
    donor = SimpleNamespace(
        households=donors, persons=seed_persons_in,
        completeness_report=SimpleNamespace(completeness_rate=1.0),
        completion_report=SimpleNamespace(n_households_filled=0, n_persons_added=0),
    )
    don_hh, don_p, seed_hh, seed_p = popsim_stage._build_populationsim_seed(
        _FakeContext(donor), sources.get_source("mid"), "mid", None, True, None,
        [_entry(n) for n in entries], np.random.RandomState(7), "H_ANZPED",
    )
    merged = pd.DataFrame({
        "ZENSUS100m": ["A"] * len(ids), "ZENSUS1km": ["KA"] * len(ids), "H_ID": ids,
        "RegionalSchlussel_ARS": ["031010000000"] * len(ids),
    })
    merged = pd.concat([merged, merged], ignore_index=True)  # two clones per donor
    merged = expand.assign_synthetic_household_ids(merged)
    # Clones are expanded from the donor persons the stage hands to the assembly.
    persons = expand.expand_to_persons(merged, don_p, donor_col="H_ID")
    persons = expand.map_demographics(persons, rng=np.random.RandomState(3))
    persons = assembly.derive_zone_ids(persons)
    clones, _map = assembly.map_mid_person_attributes(
        persons, don_hh, rng=np.random.RandomState(3),
    )
    return seed_hh.set_index(cols.household_id), seed_p, clones


def test_seed_and_clones_carry_identical_household_counts():
    """The seed value and the value on every assembled clone are the SAME draw (P3-F1)."""
    donors = _donor_households()
    seed_by_id, _seed_p, clones = _seed_and_assembled_clones(donors)
    for column in ("number_of_cars", "number_of_bicycles", "has_ebike"):
        for donor_id, value in clones.groupby("H_ID")[column].first().items():
            assert seed_by_id.loc[donor_id, column] == value, (column, donor_id)
    # The fixture really exercises the 99 path (a missing-free fixture would pass vacuously).
    assert (donors["H_ANZAUTO"] == 99).any()


def test_legacy_donor_table_carries_seed_resolved_counts(caplog):
    """complete_members=False: the separately loaded donor table takes the seed's values."""
    donors = _donor_households()
    cols = sources.get_source("mid").seed_columns()
    persons = pd.DataFrame({
        cols.person_household_id: donors[cols.household_id], cols.person_id: 1,
        cols.person_weight: 1.0, cols.age: 40, cols.sex: 1, "anzwege1": 2, "alter_gr1": 5,
    })
    # The seed as load_mid_seed builds it: its own household frame, its own rng stream.
    seed_hh, _ = mid.project_completed_seed(
        donors.iloc[:-5], persons.iloc[:-5], cols,
        kreis_control_entries=[
            _entry("number_of_cars"), _entry("number_of_bicycles"), _entry("has_ebike")],
        kreis_seed_rng=np.random.RandomState(99), ebike_seed_column="H_ANZPED",
    )

    class _Source:
        def load_donor(self, _mid_dir):
            return donors.copy(), persons, None

    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.attributes"):
        out, _ = popsim_stage._load_donor_tables(
            None, _Source(), "mid", None, False, None, None,
            seed_households=seed_hh, seed_id_col=cols.household_id,
            fallback_rng=np.random.RandomState(3),
        )
    seed_by_id = seed_hh.set_index(cols.household_id)
    in_seed = out[cols.household_id].isin(seed_by_id.index)
    assert in_seed.sum() == len(donors) - 5
    for column in attributes.HOUSEHOLD_COUNT_COLUMNS:
        got = out.loc[in_seed].set_index(cols.household_id)[column]
        assert (got == seed_by_id.loc[got.index, column]).all(), column
        assert out[column].notna().all()  # the 5 donors outside the seed got the fallback
    assert not ((out["has_ebike"] == 1) & (out["number_of_bicycles"] == 0)).any()
    assert "fallback" in " ".join(r.getMessage() for r in caplog.records)


def _donor_persons_with_imputed_codes(mid_persons):
    """Valid codes mixed with the imputed ones: P_BKAT 9, P_FKARTE 99 / 206 (and 202)."""
    n = len(mid_persons)
    out = mid_persons.copy()
    out["P_FKARTE"] = [(3, 99, 206, 5, 8, 202, 1)[i % 7] for i in range(n)]
    out["P_BKAT"] = [(1, 9, 2, 9, 3, 6, 4)[i % 7] for i in range(n)]
    return out


def test_seed_and_clones_carry_identical_person_attributes():
    """employment_status / pt_subscription_type: seed person and every clone agree (P3-F1)."""
    donors = _donor_households(70)
    _hh, seed_p, clones = _seed_and_assembled_clones(
        donors, mid_persons_extra=_donor_persons_with_imputed_codes,
        entries=("employment_status", "pt_ticket_group"),
    )
    cols = sources.get_source("mid").seed_columns()
    seed_by_id = seed_p.set_index(cols.person_household_id)
    clone_groups = attributes.map_pt_ticket_group(clones)["pt_ticket_group"]
    assert len(clones) == 2 * len(donors)
    assert (clones["employment_status"].values
            == seed_by_id.loc[clones["H_ID"], "employment_status"].values).all()
    assert (clone_groups.values
            == seed_by_id.loc[clones["H_ID"], "pt_ticket_group"].values).all()
    # The boolean is derived from the same category on every clone (ADR-0087).
    flat = clones["pt_subscription_type"].isin(attributes.PT_TICKET_FLATRATE)
    assert (clones["has_pt_subscription"] == flat).all()
    # The fixture exercises the imputed codes (a code-free fixture would pass vacuously).
    assert (clones["P_BKAT"] == 9).any() and clones["P_FKARTE"].isin([99, 206]).any()


def test_valid_person_codes_are_unchanged_by_the_single_resolution():
    """Primary path: persons with valid P_BKAT / P_FKARTE keep exactly their mapped value."""
    donors = _donor_households(70)
    _hh, _seed_p, clones = _seed_and_assembled_clones(
        donors, mid_persons_extra=_donor_persons_with_imputed_codes,
        entries=("employment_status", "pt_ticket_group"),
    )
    valid_b = clones["P_BKAT"] != 9
    expected_b = clones.loc[valid_b, "P_BKAT"].map(attributes.EMPLOYMENT_STATUS_BY_P_BKAT)
    assert (clones.loc[valid_b, "employment_status"] == expected_b).all()
    valid_f = clones["P_FKARTE"].isin(list(attributes.FKARTE_TO_CATEGORY))
    expected_f = clones.loc[valid_f, "P_FKARTE"].map(attributes.FKARTE_TO_CATEGORY)
    assert (clones.loc[valid_f, "pt_subscription_type"].astype(str) == expected_f).all()
