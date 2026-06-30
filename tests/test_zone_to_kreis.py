import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon
from braunschweig.gravity.model import _zone_to_kreis, _gemeinde_to_kreis
from braunschweig.gravity.taz_margins import taz_to_kreis_lookup


def test_default_is_legacy_prefix():
    s = pd.Series(["03101000", "03154012"])
    assert _zone_to_kreis(s).tolist() == ["03101", "03154"]


def test_gemeinde_to_kreis_shim_preserved():
    assert _gemeinde_to_kreis(pd.Series(["03101000"])).tolist() == ["03101"]   # M1: symbol kept


def test_with_lookup_maps_taz():
    lookup = {"310101901": "03101", "315400123": "03154"}
    assert _zone_to_kreis(pd.Series(["310101901", "315400123"]), lookup).tolist() == ["03101", "03154"]


def test_raises_on_unmapped():
    with pytest.raises(KeyError):
        _zone_to_kreis(pd.Series(["999999"]), {"310101901": "03101"})


def test_taz_to_kreis_lookup():
    df = gpd.GeoDataFrame({"taz_id": ["310101901"], "commune_id": ["03101000"], "kreis": ["03101"]},
                          geometry=[Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])], crs="EPSG:25832")
    assert taz_to_kreis_lookup(df) == {"310101901": "03101"}
