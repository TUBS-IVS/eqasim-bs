import sys; from pathlib import Path
REPO = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(REPO))
import pytest
F = pytest.importorskip("braunschweig.synthesis.vehicles.fleet_sampling_de")

def test_normalize_transliterates_umlauts_and_strips_suffix():
    n = F.normalize_gemeinde
    assert n("WOLFENBÜTTEL, STADT") == "WOLFENBUETTEL"
    assert n("Adenbüttel") == "ADENBUETTEL"
    assert n("BÖRSSUM") == "BOERSSUM"
    assert n("BROME,FLECKEN") == "BROME"
    assert n("BAD HARZBURG,ST.") == "BAD HARZBURG"
    assert n("HARZ (LANDKREIS GOSLAR), GEMFR. GEBIET") == "HARZ"
    assert n("  braunschweig  ") == "BRAUNSCHWEIG"

def test_matching_both_sides_gives_equal_keys():
    n = F.normalize_gemeinde
    assert n("WOLFENBÜTTEL, STADT") == n("WOLFENBUETTEL,ST.")  # pop vs FZ27.17 key
