"""Wrapper: resident vehicles + injected in-commuter car vehicles (aliases
synthesis.vehicles.vehicles). That stage returns (df_types, df_vehicles); the
in-commuter vehicles are appended to df_vehicles (tuple element 1)."""
from braunschweig.synthesis.incommuter_merge._base import make_wrapper

configure, execute = make_wrapper(
    "synthesis.vehicles.vehicles", "vehicles", "owner_id", tuple_index=1)
