"""PopulationSim workflow support for the Braunschweig pipeline.

Houses the building blocks of the ``popsim_open`` / ``popsim_mid`` workflows:
the Zensus 2022 grid cell handling (100 m / 1 km consistency), the declarative
control spec, the seed providers, and the batch/run/merge engine. Built up phase
by phase; see docs/population/ and the refactor plan.
"""
