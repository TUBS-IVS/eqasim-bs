"""Pin tests for the committed SrV trip-class tables (persons by number of
trips on the mittlerer Werktag). Reads ONLY the committed CSVs."""
from pathlib import Path

import pandas as pd
import pytest

SRV = (Path(__file__).resolve().parents[1] / "eqasim-data" / "data"
       / "braunschweig" / "srv")
CLS = ["trips_0", "trips_1_2", "trips_3_4", "trips_5plus"]
SRV_KREISE = {"03101", "03102", "03151", "03153", "03154", "03157", "03158"}


@pytest.fixture(scope="module")
def by_kreis() -> pd.DataFrame:
    return pd.read_csv(SRV / "srv2023_trip_classes_by_kreis.csv",
                       comment="#", dtype={"code": str})


@pytest.fixture(scope="module")
def by_age() -> pd.DataFrame:
    return pd.read_csv(SRV / "srv2023_trip_classes_by_age.csv", comment="#")


def test_kreis_structure(by_kreis):
    kreis = by_kreis[by_kreis["level"] == "kreis"]
    assert set(kreis["code"]) == SRV_KREISE
    assert ((by_kreis[CLS].sum(axis=1) - 1.0).abs() < 0.005).all()
    assert by_kreis["share_trips_invalid"].between(0, 0.2).all()


def test_kreis_pins(by_kreis):
    k = by_kreis[by_kreis["level"] == "kreis"].set_index("code")
    # Braunschweig: most high-mobility persons; Salzgitter: most immobile.
    assert k.loc["03101", "trips_0"] == pytest.approx(0.100, abs=0.005)
    assert k.loc["03101", "trips_5plus"] == pytest.approx(0.250, abs=0.005)
    assert k.loc["03102", "trips_0"] == pytest.approx(0.120, abs=0.005)
    t = by_kreis[by_kreis["level"] == "total"].iloc[0]
    assert t["trips_1_2"] == pytest.approx(0.336, abs=0.005)


def test_age_structure_and_pins(by_age):
    assert by_age["age_lo"].tolist() == [0, 18, 30, 50, 65, 75]
    assert ((by_age[CLS].sum(axis=1) - 1.0).abs() < 0.005).all()
    a = by_age.set_index("age_lo")
    # mobility falls sharply in old age; children rarely have 5+ trips.
    assert a.loc[75, "trips_0"] == pytest.approx(0.245, abs=0.005)
    assert a.loc[0, "trips_5plus"] == pytest.approx(0.118, abs=0.005)
    assert int(a.loc[0, "n_unweighted"]) == 3070
