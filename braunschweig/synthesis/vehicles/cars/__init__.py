"""Per-household car generation for the German (KBA/MiD) fleet (spec A4).

:mod:`braunschweig.synthesis.vehicles.cars.household` emits exactly the
household's MiD-H7 ``number_of_cars`` vehicles per household, assigns each to a
licensed adult, and types every car via the KBA/HBEFA per-vehicle generative
chain (:mod:`braunschweig.synthesis.vehicles.fleet_sampling_de`).
"""
