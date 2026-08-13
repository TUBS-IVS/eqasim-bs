from tests.conftest import popsim_stage_package_source_text


def test_stage_declares_random_seed_and_threads_rng():
    src = popsim_stage_package_source_text()
    assert 'context.config("random_seed")' in src
    # build_persons must receive the seeded rng (not fall back to RandomState(0))
    assert "rng=rng" in src
    assert "74511" in src
