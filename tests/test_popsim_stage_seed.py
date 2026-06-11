import pathlib


def test_stage_declares_random_seed_and_threads_rng():
    src = pathlib.Path("braunschweig/popsim/stage.py").read_text(encoding="utf-8")
    assert 'context.config("random_seed")' in src
    # build_persons must receive the seeded rng (not fall back to RandomState(0))
    assert "rng=rng" in src
    assert "74511" in src
