"""Curated flag registry: the ONLY config keys the GUI may edit.

Decision (spec 2026-07-08): curated toggles only -- anything else is changed
in template YAMLs in the repo. Every entry documents type, unit, valid range
and a one-line description so the GUI can render safe, self-explanatory
controls. Keys match the config_*.yml keys verbatim."""
from __future__ import annotations

from dataclasses import dataclass, field

_P = "braunschweig.population.popsim."


@dataclass(frozen=True)
class Flag:
    key: str
    group: str
    type: str                       # bool | int | float | str | choice
    description: str
    unit: str = ""
    choices: tuple = ()
    min: float | None = None
    max: float | None = None


FLAGS: list[Flag] = [
    # --- General ----------------------------------------------------------
    Flag("sampling_rate", "General", "float", "Population sampling fraction", "fraction", min=0.0, max=1.0),
    Flag("random_seed", "General", "int", "Global random seed (reproducibility)", min=0),
    Flag("processes", "General", "int", "synpp worker processes", min=1, max=64),
    Flag("output_prefix", "General", "str", "Prefix for all output files"),
    # --- MATSim runtime -----------------------------------------------------
    Flag("matsim_last_iteration", "MATSim runtime", "int", "Last MATSim iteration (0-based)", min=0),
    Flag("matsim_write_plans_interval", "MATSim runtime", "int", "Write plans every N iterations", min=0),
    Flag("matsim_write_events_interval", "MATSim runtime", "int", "Write events every N iterations (0=never)", min=0),
    Flag("matsim_threads", "MATSim runtime", "int", "MATSim global threads", min=1, max=64),
    Flag("matsim_qsim_threads", "MATSim runtime", "int", "QSim threads", min=1, max=64),
    Flag("java_memory", "MATSim runtime", "str", "JVM heap, e.g. 100G"),
    Flag("mode_choice", "MATSim runtime", "bool", "Enable eqasim discrete mode choice"),
    # --- Population ---------------------------------------------------------
    Flag("braunschweig.population.method", "Population", "choice",
         "Population synthesis method", choices=("simple_ipf_open", "popsim_open", "popsim_mid")),
    Flag(_P + "control_tiers", "Population", "str", "PopulationSim control tiers, e.g. tier0,tier1,tier2,tier3"),
    Flag(_P + "employment_grid", "Population", "choice", "Per-cell employment grid control", choices=("on", "off")),
    Flag(_P + "stratify_regiostar", "Population", "bool", "Stratify donor pool by RegioStaR7"),
    Flag(_P + "income_spatial_tilt", "Population", "bool", "Rent-based within-Kreis income tilt"),
    Flag(_P + "income_kreis_control", "Population", "bool", "Economic-status x Kreis income control"),
    Flag(_P + "num_workers", "Population", "int", "Parallel PopulationSim batches", min=1, max=62),
    Flag("home_location_sampling", "Population", "choice", "Home placement sampling", choices=("random", "weighted")),
    # --- Locations & secondary ----------------------------------------------
    Flag("work_building_potentials", "Locations", "bool", "Building-level work location weights"),
    Flag("secondary_building_potentials", "Locations", "bool", "Building-level secondary weights"),
    Flag("secondary_distance_by_purpose", "Locations", "bool", "Purpose-resolved secondary distances (Tier 1)"),
    Flag("secondary_shop_daily_split", "Locations", "bool", "Shop daily/non-daily split (Tier 2)"),
    Flag("education_gravity_enabled", "Locations", "bool", "Education gravity on real NDS schools"),
    # --- Fleet ---------------------------------------------------------------
    Flag("vehicles_method", "Fleet", "choice", "Vehicle generation method", choices=("default", "household")),
    Flag("fleet_model_enabled", "Fleet", "bool", "KBA fleet model (age/segment joint IPF)"),
    Flag("fleet_model_brands", "Fleet", "bool", "Brand assignment in the fleet model"),
    Flag("fleet_hsn_tsn_attributes", "Fleet", "bool", "HSN/TSN kW/ccm/fuel attributes"),
    # --- External demand -----------------------------------------------------
    Flag("cordon_enabled", "External demand", "bool", "Cross-cordon commuter injection"),
    Flag("freight_enabled", "External demand", "bool", "Long-haul freight injection (german-wide-freight v3)"),
    # --- Simulation extras ---------------------------------------------------
    Flag("enable_urban_parking", "Simulation extras", "bool", "Urban parking pressure (BS inner ring)"),
    Flag("remode_carless_car_legs", "Simulation extras", "bool", "Re-mode car legs of carless households"),
    Flag("simwrapper_include_matsim", "Simulation extras", "bool", "Include MATSim tabs in SimWrapper export"),
    # --- Cache share ---------------------------------------------------------
    Flag("cache_share_enabled", "Cache share", "bool", "Prime stage caches from the shared store"),
    Flag("cache_share_export", "Cache share", "bool", "Export shareable stages after a successful run"),
]

_GROUP_ORDER = ["General", "MATSim runtime", "Population", "Locations", "Fleet",
                "External demand", "Simulation extras", "Cache share"]


def by_key() -> dict[str, Flag]:
    return {f.key: f for f in FLAGS}


def groups() -> list[str]:
    return list(_GROUP_ORDER)
