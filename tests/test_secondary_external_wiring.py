from braunschweig.synthesis.locations.secondary_chainsolvers import (
    external_candidates_cordon_warning,
)


def test_cordon_warning_only_when_external_on_and_cordon_off():
    assert external_candidates_cordon_warning(external_on=True, cordon_on=False) is not None
    assert external_candidates_cordon_warning(external_on=True, cordon_on=True) is None
    assert external_candidates_cordon_warning(external_on=False, cordon_on=False) is None
