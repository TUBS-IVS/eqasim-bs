"""Vehicle fleet synthesis (KBA / MiD-grounded German fleet).

This package builds a realistic German vehicle fleet for the synthetic
population: each household car is given a segment (this module's
:mod:`braunschweig.synthesis.vehicles.segment` income-coupled IPF), and -- in
later tasks -- a powertrain, Euro class, age, brand/model and a matching HBEFA
``VehicleType``. See ``docs/superpowers/specs/2026-06-07-fleet-kba-mid-design.md``.
"""
