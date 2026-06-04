"""Tests for the Zensus 2022 Gemeinde-share path of the population margin.

Item #5: replace the scraped, non-redistributable urbistat Gemeinde shares with
open Zensus 2022 1000A-3082. Verify the pure share helper preserves per-Kreis
totals (shares sum to 1) and that the DESTATIS->Zensus age mapping fixes the
school-age band smear (DESTATIS 10 -> Zensus 10-15, not urbistat 12-17).
"""
from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.data.census import population as pop


def _zensus_frame() -> pd.DataFrame:
    # One Kreis 03101 with two communes; the households_size_age schema
    # (persons per commune x sex x ALTKL2 band x hh_size).
    return pd.DataFrame([
        # commune,        sex,    lower_age, upper_age, hh_size, weight
        ["031010000001", "male",   10, 15, "5", 60.0],
        ["031010000001", "male",   10, 15, "2", 20.0],   # commune1 children: 80
        ["031010000002", "male",   10, 15, "4", 20.0],   # commune2 children: 20
        ["031010000001", "female", 75, 150, "1", 30.0],
        ["031010000002", "female", 75, 150, "1", 10.0],
    ], columns=["commune_id", "sex", "lower_age", "upper_age", "hh_size", "weight"])


class TestDestatisToZensusAge:
    def test_school_age_band_maps_to_native_zensus_band(self) -> None:
        # The urbistat path smeared DESTATIS 10 (ages 10-14) into 12-17; the
        # Zensus path maps it to the native 10-15 band.
        assert pop.DESTATIS_TO_ZENSUS_AGE[10] == 10
        assert pop.DESTATIS_TO_URBISTAT_AGE[10] == 12  # the legacy smear

    def test_every_destatis_class_maps_to_a_zensus_band(self) -> None:
        for d in pop.DESTATIS_AGES:
            assert pop.DESTATIS_TO_ZENSUS_AGE[d] in pop.ZENSUS_AGES


class TestLoadZensusShares:
    def test_shares_sum_to_one_per_kreis_sex_band(self) -> None:
        shares = pop._load_zensus_shares(_zensus_frame(), {"03101"})
        grp = shares.groupby(["kreis", "sex", "z_age"])["share"].sum()
        assert grp.loc[("03101", "male", 10)] == pytest.approx(1.0)
        assert grp.loc[("03101", "female", 75)] == pytest.approx(1.0)

    def test_aggregates_over_hh_size(self) -> None:
        shares = pop._load_zensus_shares(_zensus_frame(), {"03101"})
        # commune1 has 60+20=80 of 100 male children -> share 0.8.
        c1 = shares[(shares["commune_id"] == "031010000001")
                    & (shares["sex"] == "male") & (shares["z_age"] == 10)]
        assert c1["share"].iloc[0] == pytest.approx(0.8)

    def test_carries_twelve_digit_commune_id_no_name_match(self) -> None:
        shares = pop._load_zensus_shares(_zensus_frame(), {"03101"})
        assert set(shares["commune_id"]) == {"031010000001", "031010000002"}

    def test_out_of_scope_kreis_dropped(self) -> None:
        shares = pop._load_zensus_shares(_zensus_frame(), {"03999"})
        assert shares.empty

    def test_per_kreis_total_preserved_when_combined_with_destatis(self) -> None:
        # Multiply DESTATIS Kreis totals by the shares: the per-Kreis sum must
        # equal the DESTATIS total (shares sum to 1 per kreis x sex x band).
        shares = pop._load_zensus_shares(_zensus_frame(), {"03101"})
        # DESTATIS: 100 male children (d_age 10) + 40 female elderly (d_age 75).
        destatis = pd.DataFrame([
            ["03101", "male", 10, 100.0],
            ["03101", "female", 75, 40.0],
        ], columns=["kreis", "sex", "z_age", "destatis_weight"])
        m = shares.merge(destatis, on=["kreis", "sex", "z_age"])
        m["weight"] = m["share"] * m["destatis_weight"]
        assert m["weight"].sum() == pytest.approx(140.0)
