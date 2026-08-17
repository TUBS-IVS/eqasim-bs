"""Gemeinde-name canonicalisation for the KBA Gemeinde-tilt join (issue #161).

Covers the single canonical normaliser
(:func:`braunschweig.synthesis.vehicles.fleet_sampling_de.normalize_gemeinde_name`)
and the Gebietsstand crosswalk that maps a Gebietsstand-2020 population label
onto the successor Gemeinde of a municipal merger.

Measured effect of these rules on the real ZGB vocabularies (KBA FZ 27.17 keys
vs the 126 BBSR RegioStaR ``name_20`` labels): 116/116 populated Gemeinden
matched, zero false matches. See ADR-0082.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest

F = pytest.importorskip("braunschweig.synthesis.vehicles.fleet_sampling_de")


def test_normalize_transliterates_umlauts_and_strips_status_suffix():
    n = F.normalize_gemeinde_name
    assert n("WOLFENBÜTTEL, STADT") == "WOLFENBUETTEL"
    assert n("Adenbüttel") == "ADENBUETTEL"
    assert n("BÖRSSUM") == "BOERSSUM"
    assert n("BROME,FLECKEN") == "BROME"
    assert n("BAD HARZBURG,ST.") == "BAD HARZBURG"
    assert n("  braunschweig  ") == "BRAUNSCHWEIG"


def test_normalize_strips_long_form_suffix_and_parenthetical():
    """RegioStaR carries suffixes beyond STADT/ST/FLECKEN and parentheticals."""
    n = F.normalize_gemeinde_name
    assert n("CLAUSTHAL-ZELLERFELD, BERG- UND UNIVERSITÄTSSTADT") == "CLAUSTHAL-ZELLERFELD"
    # The FZ 27.17 sheet writes this one WITHOUT the space before the bracket.
    assert n("MÜDEN (ALLER)") == n("MUEDEN(ALLER)") == "MUEDEN"
    assert n("Veltheim (Ohe)") == "VELTHEIM"


def test_matching_both_sides_gives_equal_keys():
    n = F.normalize_gemeinde_name
    assert n("WOLFENBÜTTEL, STADT") == n("WOLFENBUETTEL,ST.")  # pop vs FZ 27.17 key


def test_gemeindefreies_gebiet_is_excluded_not_folded_onto_a_town():
    """A gemeindefreies Gebiet has no KBA row; folding it onto the neighbouring
    town would silently hand it that town's EV tilt (a false match)."""
    n = F.normalize_gemeinde_name
    assert n("SCHÖNINGEN, GEMFR. GEBIET") == ""
    assert n("HARZ (LANDKREIS GOSLAR), GEMFR. GEBIET") == ""
    assert n("Voigtsdahlum, gemfr. Gebiet") == ""
    # ... and specifically NOT the real town's key.
    assert n("SCHÖNINGEN, GEMFR. GEBIET") != n("SCHÖNINGEN, STADT")
    assert n("HELMSTEDT, GEMFR. GEBIET") != n("HELMSTEDT, STADT")


def test_missing_gemeinde_returns_empty_key():
    n = F.normalize_gemeinde_name
    assert n(None) == ""
    assert n(float("nan")) == ""


def test_gebietsstand_crosswalk_maps_merged_gemeinden_to_successor():
    """Lutter am Barenberge / Hahausen / Wallmoden merged into Stadt Langelsheim
    on 1 November 2021 -- after the population's Gebietsstand 2020, before the
    KBA vintages. Their labels must reach Langelsheim's reference row."""
    n = F.normalize_gemeinde_name
    cw = F.apply_gebietsstand_crosswalk
    for name in ("Hahausen", "Lutter am Barenberge, Flecken", "Wallmoden"):
        assert cw("03153", n(name)) == "LANGELSHEIM"


def test_gebietsstand_crosswalk_is_a_no_op_elsewhere():
    """The crosswalk is keyed by (Kreis, name): it must not rewrite a same-named
    Gemeinde in another Kreis, nor any unmerged Gemeinde."""
    n = F.normalize_gemeinde_name
    cw = F.apply_gebietsstand_crosswalk
    assert cw("03151", n("Hahausen")) == "HAHAUSEN"
    assert cw("03153", n("Goslar, Stadt")) == "GOSLAR"
    assert cw("03158", n("Wolfenbüttel, Stadt")) == "WOLFENBUETTEL"
    assert cw("03153", "") == ""
