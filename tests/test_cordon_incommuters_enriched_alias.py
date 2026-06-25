"""Regression test: the cordon in-commuter stage must depend on the ALIASED
``synthesis.population.enriched`` rather than the hard-coded IPF module
``braunschweig.synthesis.population.enriched``.

Root cause this guards against (run 2026-06-23, popsim_mid 25% all-features):
``braunschweig.synthesis.incommuters`` pulled ``braunschweig.synthesis.population.enriched``
by its real module name, bypassing the config alias. That IPF module delegates its
``configure`` to the eqasim core ``synthesis.population.enriched``, which depends on
``synthesis.population.matched`` (HTS statistical matching). The whole eqasim HTS
subtree was therefore dragged into every popsim run and failed in ``matched`` because
the popsim producer does not attach the ``urban_class`` matching attribute.

The stage only needs the maximum resident ``person_id`` / ``household_id`` for
in-commuter id collision avoidance (execute(): ``residents["person_id"].max()`` /
``residents["household_id"].max()``); both the popsim ``enriched_adapter`` and the IPF
``braunschweig.synthesis.population.enriched`` provide those columns. Depending on the
aliased ``synthesis.population.enriched`` therefore tracks whichever population the run
config selects (popsim adapter or IPF), keeps IPF runs byte-identical, and is in fact
more correct for popsim (it counts the population actually written to MATSim).
"""
from __future__ import annotations

import braunschweig.synthesis.incommuters as incommuters


class FakeContext:
    """Minimal synpp PrepareContext stub recording context.stage() names."""

    def __init__(self, config: dict):
        self._config = config
        self.stages: list[str] = []

    def config(self, key, default=None):
        return self._config.get(key, default)

    def stage(self, name, alias=None, **kwargs):
        self.stages.append(name)
        return None


def _configure_stages(real_origin: bool) -> list[str]:
    # cordon_enabled must be True, else configure() returns early before declaring
    # any stage dependency (incommuters.configure line 962-963).
    ctx = FakeContext({"cordon_enabled": True,
                       "cordon_incommuter_real_origin": real_origin})
    incommuters.configure(ctx)
    return ctx.stages


def test_incommuters_depends_on_aliased_enriched_not_hardcoded_ipf_module():
    # Both branches of the real_origin gate must declare the aliased dependency.
    for real_origin in (False, True):
        stages = _configure_stages(real_origin)
        assert "synthesis.population.enriched" in stages, (
            "in-commuter stage must depend on the ALIASED synthesis.population.enriched "
            "so it tracks the config-selected population (popsim adapter or IPF)"
        )
        assert "braunschweig.synthesis.population.enriched" not in stages, (
            "hard-coding the IPF module bypasses the alias and pulls the eqasim "
            "HTS-matching subtree (synthesis.population.matched) into popsim runs"
        )
