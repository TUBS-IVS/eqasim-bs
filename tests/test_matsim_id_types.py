"""Tests for the Long-safe id-type helper and the MATSim population/households writers.

Bug D2: census_person_id / census_household_id / hts_id / hts_household_id were
written as java.lang.Long even when the values are alphanumeric popsim provenance
strings (e.g. "10N548_E43_1234_0_1"). MATSim crashes on scenario load because it
cannot parse a non-numeric string as Long.

Fix: writers.long_or_string_type() selects "java.lang.Long" when the value is an
integer, and "java.lang.String" otherwise. The default pipeline uses integer ids so
its output is byte-identical; popsim_mid uses alphanumeric ids and now gets String.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import matsim.writers as writers  # noqa: E402
import matsim.scenario.population as pop  # noqa: E402
import matsim.scenario.households as hh  # noqa: E402


# ---------------------------------------------------------------------------
# Unit tests for long_or_string_type
# ---------------------------------------------------------------------------

def test_long_for_integer_id():
    """Integer values (native int and numeric-string) -> java.lang.Long."""
    assert writers.long_or_string_type(5) == "java.lang.Long"
    assert writers.long_or_string_type("12345") == "java.lang.Long"


def test_string_for_alphanumeric_id():
    """Alphanumeric provenance strings -> java.lang.String."""
    assert writers.long_or_string_type("10N548_E43_1234_0_1") == "java.lang.String"
    assert writers.long_or_string_type("abc") == "java.lang.String"


def test_long_for_zero():
    """Edge case: 0 is a valid integer."""
    assert writers.long_or_string_type(0) == "java.lang.Long"
    assert writers.long_or_string_type("0") == "java.lang.Long"


def test_string_for_none():
    """None is not a valid integer -> java.lang.String (defensive)."""
    assert writers.long_or_string_type(None) == "java.lang.String"


def test_string_for_compound_id():
    """Compound popsim id with underscores -> java.lang.String."""
    assert writers.long_or_string_type("ZENSUS100m_12345_0_678") == "java.lang.String"


# ---------------------------------------------------------------------------
# Unit tests for column_java_type (per-COLUMN type decision)
# ---------------------------------------------------------------------------

def test_column_java_type_int_column_is_long():
    import pandas as pd
    from matsim import writers
    assert writers.column_java_type(pd.Series([1, 2, 3])) == "java.lang.Long"


def test_column_java_type_float_column_raises():
    import pandas as pd, pytest
    from matsim import writers
    with pytest.raises(ValueError):
        writers.column_java_type(pd.Series([1.0, float("nan"), 3.0]))


def test_column_java_type_leading_zero_string_is_string():
    import pandas as pd
    from matsim import writers
    assert writers.column_java_type(pd.Series(["03101", "03102"])) == "java.lang.String"


def test_column_java_type_pure_digit_string_is_long():
    import pandas as pd
    from matsim import writers
    assert writers.column_java_type(pd.Series(["12345", "678"])) == "java.lang.Long"


def test_column_java_type_alnum_string_is_string():
    import pandas as pd
    from matsim import writers
    assert writers.column_java_type(pd.Series(["ZENSUS100m_E43_1", "x"])) == "java.lang.String"


# ---------------------------------------------------------------------------
# Integration: population writer uses Long for integer census/hts ids (DEFAULT)
# ---------------------------------------------------------------------------

def _write_person_xml(census_person_id, census_household_id,
                      hts_id, hts_household_id):
    """Write a minimal one-person population XML and return the decoded text."""
    from shapely.geometry import Point

    defaults = {
        "person_id": 1, "household_income": "3000", "car_availability": "none",
        "bicycle_availability": "none",
        "census_household_id": census_household_id,
        "census_person_id": census_person_id,
        "household_id": 1,
        "has_license": True, "has_pt_subscription": False,
        "hts_id": hts_id, "hts_household_id": hts_household_id,
        "age": 40, "employed": "yes", "sex": "male",
        "high_income": False, "is_urban_resident": False,
        "pt_subscription_type": "never_pt",
        "household_income_eur": 3000.0,
    }
    person = tuple(defaults[f] for f in pop.PERSON_FIELDS)
    activity = tuple(
        {"person_id": 1, "start_time": float("nan"), "end_time": float("nan"),
         "purpose": "home", "geometry": Point(0.0, 0.0), "location_id": -1}[f]
        for f in pop.ACTIVITY_FIELDS
    )
    buf = io.BytesIO()
    w = writers.PopulationWriter(buf)
    w.start_population()
    pop.add_person(w, person, [activity], [], [],
                   enable_urban_parking=False, write_income_eur=False)
    w.end_population()
    return buf.getvalue().decode("utf-8")


def test_population_writer_integer_census_id_emits_long():
    """DEFAULT path: integer census/hts ids -> java.lang.Long (byte-identical)."""
    xml = _write_person_xml(
        census_person_id=42, census_household_id=7,
        hts_id=101, hts_household_id=5,
    )
    # All four id attributes must use java.lang.Long
    assert 'name="censusPersonId" class="java.lang.Long"' in xml
    assert 'name="censusHouseholdId" class="java.lang.Long"' in xml
    assert 'name="htsPersonId" class="java.lang.Long"' in xml
    assert 'name="htsHouseholdId" class="java.lang.Long"' in xml


def test_population_writer_alphanumeric_census_id_emits_string():
    """popsim_mid path: alphanumeric census ids -> java.lang.String."""
    xml = _write_person_xml(
        census_person_id="ZENSUS100m_12345_0_678",
        census_household_id="ZENSUS100m_12345_0",
        hts_id=101,        # MiD P_ID is numeric -> still Long
        hts_household_id=5,
    )
    assert 'name="censusPersonId" class="java.lang.String"' in xml
    assert 'name="censusHouseholdId" class="java.lang.String"' in xml
    # numeric hts ids remain Long
    assert 'name="htsPersonId" class="java.lang.Long"' in xml
    assert 'name="htsHouseholdId" class="java.lang.Long"' in xml


def test_population_writer_alphanumeric_hts_id_emits_string():
    """If hts_id is alphanumeric (unusual but possible), it also gets String."""
    xml = _write_person_xml(
        census_person_id=42,
        census_household_id=7,
        hts_id="P_abc_1",
        hts_household_id="H_abc",
    )
    assert 'name="htsPersonId" class="java.lang.String"' in xml
    assert 'name="htsHouseholdId" class="java.lang.String"' in xml
    # integer census ids remain Long
    assert 'name="censusPersonId" class="java.lang.Long"' in xml


# ---------------------------------------------------------------------------
# Integration: households writer uses Long for integer census_household_id (DEFAULT)
# ---------------------------------------------------------------------------

def _write_household_xml(census_household_id):
    """Write a minimal one-household XML and return the decoded text."""
    from shapely.geometry import Point

    # Build a minimal persons frame with the FIELDS the households writer needs.
    import pandas as pd
    df = pd.DataFrame([{
        "household_id": 1, "person_id": 1, "household_income": "3000",
        "high_income": False, "car_availability": "none",
        "bicycle_availability": "none",
        "census_household_id": census_household_id,
    }])

    buf = io.BytesIO()

    import gzip
    # Write without gzip for test simplicity: the writer accepts any file-like object.
    class _FakeContext:
        def progress(self, total, label=""):
            import contextlib
            @contextlib.contextmanager
            def _ctx():
                class _P:
                    def update(self): pass
                yield _P()
            return _ctx()
        def path(self):
            return "/tmp"

    buf2 = io.BytesIO()
    w = writers.HouseholdsWriter(buf2)
    w.start_households()
    hh.add_household(w, tuple(df[hh.FIELDS].iloc[0]), [1])
    w.end_households()
    return buf2.getvalue().decode("utf-8")


def test_households_writer_integer_census_id_emits_long():
    """DEFAULT path: integer census_household_id -> java.lang.Long."""
    xml = _write_household_xml(census_household_id=99)
    assert 'name="censusId" class="java.lang.Long"' in xml


# ---------------------------------------------------------------------------
# Regression: add_leg must not serialize a missing time as the literal "None"
# ---------------------------------------------------------------------------
# A cross-cordon in-commuter leg reached the writer with a missing departure_time,
# and add_leg emitted dep_time="None" -> MATSim RunPreparation rejects it with
# NumberFormatException ("For input string: None"). A missing time (Python None OR
# NaN -- NaN is what actually reaches the writer for an unrouted leg) must be
# omitted, mirroring add_activity's handling of a missing start_time/end_time.

def _write_one_leg_xml(departure_time, travel_time):
    """Drive the population writer through a single leg; return the decoded XML."""
    buf = io.BytesIO()
    w = writers.PopulationWriter(buf)
    w.start_population()
    w.start_person(1)
    w.start_plan(selected=True)
    w.add_leg("car", departure_time, travel_time)
    w.end_plan()
    w.end_person()
    w.end_population()
    return buf.getvalue().decode("utf-8")


def test_add_leg_omits_python_none_times():
    """Python None -> attributes omitted, never the crashing literal 'None'."""
    xml = _write_one_leg_xml(None, None)
    assert '<leg mode="car"' in xml
    assert 'dep_time="None"' not in xml
    assert 'trav_time="None"' not in xml
    assert "dep_time=" not in xml
    assert "trav_time=" not in xml


def test_add_leg_omits_nan_times():
    """NaN (the value that actually reaches the writer for an unrouted in-commuter
    leg) -> attributes omitted, never dep_time="None"."""
    xml = _write_one_leg_xml(float("nan"), float("nan"))
    assert '<leg mode="car"' in xml
    assert 'dep_time="None"' not in xml
    assert 'trav_time="None"' not in xml
    assert "dep_time=" not in xml
    assert "trav_time=" not in xml


def test_add_leg_writes_present_times():
    """Real times are still written -- the guard only suppresses missing ones."""
    xml = _write_one_leg_xml(3600.0, 600.0)  # 1 h departure, 10 min travel
    assert 'dep_time="01:00:00"' in xml
    assert 'trav_time="00:10:00"' in xml


def test_households_writer_alphanumeric_census_id_emits_string():
    """popsim_mid path: alphanumeric census_household_id -> java.lang.String."""
    xml = _write_household_xml(census_household_id="ZENSUS100m_12345_0")
    assert 'name="censusId" class="java.lang.String"' in xml
