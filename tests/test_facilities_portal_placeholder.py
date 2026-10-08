"""The coordinate-only placeholder id of the portal gates is not a dangling facility id (eqasim-bs#442)."""
import logging

import pandas as pd
import pytest

from braunschweig.matsim.scenario.facilities import validate_secondary_coverage


def test_the_placeholder_minus_one_of_portal_rows_passes_the_coverage_check(caplog):
    realised = pd.DataFrame({"location_id": pd.Series([5, -1], dtype=object)})
    secondary = pd.DataFrame({"location_id": [5]})
    with caplog.at_level(logging.INFO):
        validate_secondary_coverage(realised, secondary)
    assert any("1 coordinate-only" in message for message in caplog.messages)


@pytest.mark.parametrize("placeholder", [-1, -1.0, "-1"])
def test_the_placeholder_is_recognised_in_every_representation(placeholder):
    realised = pd.DataFrame({"location_id": pd.Series(["sec_1", placeholder], dtype=object)})
    validate_secondary_coverage(realised, pd.DataFrame({"location_id": ["sec_1"]}))


def test_a_genuinely_dangling_id_still_raises_next_to_the_placeholder():
    realised = pd.DataFrame({"location_id": pd.Series([5, -1, 9], dtype=object)})
    secondary = pd.DataFrame({"location_id": [5]})
    with pytest.raises(RuntimeError, match="1 realised secondary location id"):
        validate_secondary_coverage(realised, secondary)
