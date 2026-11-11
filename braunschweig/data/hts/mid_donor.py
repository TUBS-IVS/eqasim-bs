"""MiD 2023 in-commuter HTS donor stage.

Returns (households, persons, trips) shaped like eqasim's data.hts.selected, but
sourced from the German MiD survey so the cordon in-commuter stages (SvB +
student) draw their trip TIMING from German behaviour instead of the French
ENTD. Distance/mode/count/origin of in-commuters come from other (German)
sources and are unaffected. Reuses the tested popsim MiD machinery
(braunschweig.popsim.attributes.map_employed/map_studies,
braunschweig.popsim.trips.build_trip_table); no reimplementation. See
docs/superpowers/specs/2026-07-18-incommuter-mid-donor-design.md.
"""
from __future__ import annotations

import logging

import numpy as np

from braunschweig import synpp_tokens
# KEY_MID comes from the leaf config-key module, not through the popsim stage package: this
# stage shares the key, never the popsim stage's code, so its token must not hash that stage.
from braunschweig.popsim.stage import config_keys as _popsim_config_keys
from braunschweig.popsim.stage.config_keys import KEY_MID

_log = logging.getLogger(__name__)

# Canonical committed MiD 2023 raw survey directory, identical to the default
# every popsim config uses for KEY_MID. Cordon in-commuter donor draws from
# this stage whenever cordon_enabled is set (braunschweig/population/pipeline
# in-commuter stages), independent of population.method / hts choice, so a
# config that enables cordon but never sets mid_raw_path explicitly (e.g. an
# ENTD-resident config) must still resolve to a usable path instead of
# hard-failing at synpp graph-build.
DEFAULT_MID_RAW_PATH = "eqasim-data/data/braunschweig/popsim/mid2023_raw"
KEY_DEMOGRAPHICS = "cordon_incommuter_donor_demographics"

# ASSUMPTION (a warn bound, not a published reference value): the share of donor persons whose HP_SEX
# is neither 1 (male) nor 2 (female) above which ``_map_donor_demographics`` reads the imputation as a
# coding problem and warns. MiD 2023 itself carries such codes by construction (3 'divers', 9 'keine
# Angabe'), so a small share is information, not a fallback to worry about. The share measured on the
# raw file is recorded in docs/runs/prod100-expected-message-levels-2026-11-04.yml; the bound leaves a
# margin of several times over it, so only a change of the coding or of the file reaches it.
SEX_IMPUTED_WARN_SHARE = 0.01

# Module-level dependencies whose source can change this stage's result without
# changing this file. Keep these as module objects to match their import site.
_HELPER_MODULES = (
    _popsim_config_keys,
)

# Function-level dependencies whose transformations live outside the stage file
# synpp hashes itself. The MiD source adapter loads the donor tables through
# braunschweig.popsim.mid.donor.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.popsim.expand",
    "braunschweig.popsim.attributes",
    "braunschweig.popsim.trips",
    "braunschweig.popsim.sources.mid",
    "braunschweig.popsim.mid.donor",
    # The donor household income draw (ADR-0191, attach_donor_household).
    "braunschweig.popsim.placement_income",
    # The donor household size / consumption units and the donor draw attributes.
    "braunschweig.popsim.diary_facts",
    "braunschweig.popsim.income_kreis_control",
    "braunschweig.data.mid.income_by_size",
    "braunschweig.data.mid.income_by_status",
    "braunschweig.data.mid.raumtyp_tilt",
    # The rest of this stage's import closure: modules its helpers import, whose code this
    # stage runs without importing it itself (tests/test_audit_synpp_helper_hash.py, ADR-0136).
    "braunschweig.data.bbsr.regiostar",
    "braunschweig.data.mid.reference_tables",
    "braunschweig.data.mid.status_by_hhtype",
    "braunschweig.popsim.assembly",
    "braunschweig.popsim.chain_matching",
    "braunschweig.popsim.closure_dwell",
    "braunschweig.popsim.day_type",
    "braunschweig.popsim.escort_pairing",
    "braunschweig.popsim.income",
    "braunschweig.popsim.member_completion",
    "braunschweig.popsim.mid.csv_format",
    "braunschweig.popsim.missing",
    "braunschweig.popsim.passenger_availability",
    "braunschweig.popsim.plan_validation",
    "braunschweig.popsim.sampling",
    "braunschweig.popsim.seed",
    "braunschweig.popsim.stratum",
    "braunschweig.popsim.time_imputation",
    "braunschweig.popsim.weekend_plan_match",
    "braunschweig.population.methods",
    "braunschweig.population.schema",
    "braunschweig.population.socioprofessional_class",
    "braunschweig.resources",
    "data.hts.egt.cleaned",
    "data.hts.entd.cleaned",
    "data.hts.hts",
    "synthesis.population.matched",
)

# Stage modules this stage's imports reach although it never runs their code; the
# import-closure gate stops at them (ADR-0136). As for braunschweig.popsim.stage, the MiD
# source adapter imports the trips stage only for build_trips, which only the trips stage
# calls: this stage loads the donor tables through the adapter and builds its trip table with
# braunschweig.popsim.trips.build_trip_table.
_TOKEN_CLOSURE_BOUNDARIES = {
    "braunschweig.popsim.trips_stage": (
        "reached only through the MiD source adapter's build_trips, which only the trips "
        "stage calls; this stage loads donors through the adapter and never builds trips with it"
    ),
}


def validate(context):
    """Invalidate cached donors when a directly used MiD helper changes.

    Returns the structured token of ``braunschweig.synpp_tokens.source_token``: one semantic
    digest per helper module (docstrings, comments and formatting do not change it).
    """
    return synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")


def _map_donor_demographics(persons, rng, *, sex_imputed_warn_share=SEX_IMPUTED_WARN_SHARE):
    """Use the resident MiD coding, without accepting an all-default donor pool.

    The rate of persons whose sex is imputed (HP_SEX other than 1 / 2) is always logged. It is a
    warning only above ``sex_imputed_warn_share`` (see :data:`SEX_IMPUTED_WARN_SHARE`): MiD 2023 carries
    such codes by construction, so a share within the bound is information.
    """
    from braunschweig.popsim.expand import map_demographics

    required = {"HP_ALTER", "HP_SEX"}
    missing = required - set(persons.columns)
    if missing:
        raise ValueError(f"[mid_donor] missing raw MiD demographics: {sorted(missing)}")
    age = persons["HP_ALTER"].to_numpy(dtype=float)
    if not (np.isfinite(age) & (age >= 0) & (age == np.floor(age))).all():
        raise ValueError("[mid_donor] HP_ALTER must contain finite non-negative integer ages")
    observed = persons["HP_SEX"].isin([1, 2])
    n, n_primary = len(persons), int(observed.sum())
    n_fallback = n - n_primary
    above_bound = n_fallback > sex_imputed_warn_share * n
    log = _log.warning if above_bound else _log.info
    log("[mid_donor] sex: primary %d/%d (%.1f%%), fallback %d/%d (%.1f%%); "
        "non-binary/missing codes use the observed MiD pool, seeded by random_seed; %s %.1f%%%s",
        n_primary, n, 100.0 * n_primary / max(n, 1),
        n_fallback, n, 100.0 * n_fallback / max(n, 1),
        "ABOVE the expected bound of" if above_bound else "within the expected bound of",
        100.0 * sex_imputed_warn_share,
        ": check the HP_SEX coding of the MiD person file" if above_bound
        else " (MiD 2023 carries such codes by construction)")
    if n and not n_primary:
        raise ValueError("[mid_donor] HP_SEX has no observed binary-sex pool for imputation")
    # A missing age-band key must use the global observed pool, not be skipped by
    # pandas groupby in the shared mapper. Give just those rows an empty band.
    mapping_input = persons.copy()
    if "alter_gr1" in mapping_input:
        mapping_input["alter_gr1"] = mapping_input["alter_gr1"].astype(object)
        mapping_input.loc[~observed & mapping_input["alter_gr1"].isna(), "alter_gr1"] = "missing"
    mapped = map_demographics(mapping_input, rng=rng)
    if not mapped["sex"].isin(["male", "female"]).all():
        raise ValueError("[mid_donor] HP_SEX mapping left unresolved donor demographics")
    _log.info("[mid_donor] age: primary %d/%d (%.1f%%), fallback 0/%d (0.0%%)",
              n, n, 100.0 if n else 0.0, n)
    # Preserve the raw donor attributes, including the original age-band keys.
    out = persons.copy()
    out[["age", "sex"]] = mapped[["age", "sex"]]
    return out


def build_mid_donor_frames(households, persons, wege, rng, *, preserve_demographics=True):
    """Pure transform: raw MiD (households, persons, wege) -> in-commuter donor
    (households, persons, trips).

    ``persons`` gets a stable per-(H_ID, P_ID) ``person_id`` plus boolean
    ``employed``/``studies`` and MiD ``age``/``sex`` via the shared mappers.
    ``preserve_demographics=False`` retains the legacy optional-column adapter,
    including its downstream scalar defaults on raw input (#397). ``trips``
    is the eqasim trip table (home-first, purposes + float departure/arrival
    seconds) built by the shared MiD trip-table builder. ``households`` is
    passed through unchanged (only used here for the donor-pool size log).

    Raises
    ------
    ValueError
        If (H_ID, P_ID) does not uniquely identify a person (person_id
        collision); this would silently corrupt the donor join downstream.
    """
    from braunschweig.popsim import attributes, trips as trips_mod

    p = persons.copy()
    p["person_id"] = p["H_ID"].astype(str) + "_" + p["P_ID"].astype(str)
    if not p["person_id"].is_unique:
        raise ValueError("[mid_donor] non-unique (H_ID, P_ID) -> person_id collision")
    p = attributes.map_employed(p, rng=rng)
    p = attributes.map_studies(p)
    if preserve_demographics:
        # Map after employment so its imputation retains the previous RNG stream.
        p = _map_donor_demographics(p, rng)
    else:
        for column in ("age", "sex"):
            n_primary = len(p) if column in p else 0
            _log.warning(
                "[mid_donor] legacy %s: primary %d/%d (%.1f%%), fallback %d/%d (%.1f%%); "
                "missing columns trigger worker/student scalar defaults",
                column, n_primary, len(p), 100.0 * n_primary / max(len(p), 1),
                len(p) - n_primary, len(p), 100.0 * (len(p) - n_primary) / max(len(p), 1))

    trips = trips_mod.build_trip_table(
        p[["person_id", "H_ID", "P_ID"]], wege,
        household_col="H_ID", person_col="P_ID", trip_col="W_ID")

    keep = ["person_id", "employed", "studies"]
    for optional_col in ("age", "sex"):
        if optional_col in p.columns:
            keep.append(optional_col)
    persons_out = p[keep].reset_index(drop=True)

    # No-silent-fallback observability (CLAUDE.md): the donor pool that later
    # cordon in-commuter stages draw commute timing from is exactly the set of
    # persons with a work/education trip leg here; a collapsed pool would
    # silently starve the in-commuter timing draw.
    n_work = trips[trips["following_purpose"] == "work"]["person_id"].nunique()
    n_edu = trips[trips["following_purpose"] == "education"]["person_id"].nunique()
    _log.info(
        "[mid_donor] %d households, %d persons, %d trips; donor pool: %d with a "
        "work leg, %d with an education leg",
        len(households), len(persons_out), len(trips), n_work, n_edu)
    return households, persons_out, trips


#: Offset of the RNG stream of :func:`attach_donor_household` from ``random_seed``, so its
#: draws never shift the stream of :func:`build_mid_donor_frames` (whose bytes are pinned, #397).
DONOR_HOUSEHOLD_INCOME_RNG_OFFSET = 52813

#: MiD household columns holding the interview ages of the household members 1..6 (codeplan:
#: 0..85, 999 = no answer, 3302..3306 = person n not in the household).
HOUSEHOLD_AGE_COLUMNS = tuple(f"HP_ALTER_{i}" for i in range(1, 7))

#: MiD household columns the donor household income, status, size and consumption units are
#: read from (ADR-0191).
DONOR_HOUSEHOLD_INCOME_COLUMNS = ("H_ID", "H_GR", "hheink_gr1", "oek_status") + HOUSEHOLD_AGE_COLUMNS

#: ASSUMPTION (a raise bound, not a reference value): share of household members without a valid
#: age above which the household-age join is read as broken and the stage raises.
HOUSEHOLD_AGE_UNKNOWN_RAISE_SHARE = 0.01

#: Codeplan range of a valid interview age (85 = 85 and older).
_VALID_AGE_RANGE = (0, 85)

#: ASSUMPTION (spec "Assumptions that remain"): a household member without a valid age counts as
#: 14 or older in the OECD-modified scale; 14 is the youngest age counted as such by
#: ``data.hts.hts.calculate_consumption_units``.
_AGE_OF_MEMBER_WITHOUT_VALID_AGE = 14

#: ASSUMPTION (a raise bound, not a reference value): share of donor persons without a MiD
#: household record above which the person-household join is read as broken and the stage raises.
DONOR_HOUSEHOLD_MISSING_RAISE_SHARE = 0.01


def _load_household_ages(mid_dir):
    """Read ``H_ID`` and :data:`HOUSEHOLD_AGE_COLUMNS` from ``MiD2023_Haushalte.csv``.

    It lives in this stage on purpose: the shared loader
    ``braunschweig.popsim.mid.donor.load_mid_attributes`` is hashed by every popsim stage, so a
    change there would re-key their caches for columns only this stage reads.
    """
    import os

    import pandas as pd

    from braunschweig.popsim.mid.csv_format import detect_csv_separator

    path = os.path.join(mid_dir, "MiD2023_Haushalte.csv")
    return pd.read_csv(
        path, sep=detect_csv_separator(path), usecols=["H_ID", *HOUSEHOLD_AGE_COLUMNS])


def _household_consumption_units(
        households, age_unknown_raise_share=HOUSEHOLD_AGE_UNKNOWN_RAISE_SHARE):
    """OECD-modified consumption units per household, from ``H_GR`` and ``HP_ALTER_1..6``.

    Members = ``H_GR`` (top-coded at 6). Valid ages are 0..85; 999 and 3302..3306 are not.
    A member without a valid age counts as 14 or older (ASSUMPTION, counted and logged as the
    fallback). The coefficients come from ``data.hts.hts.calculate_consumption_units``.
    """
    import pandas as pd

    from data.hts.hts import calculate_consumption_units

    size = pd.to_numeric(households["H_GR"], errors="coerce")
    if (size.isna() | (size < 1) | (size != np.floor(size))).any():
        raise ValueError(
            "[mid_donor] H_GR must be a positive integer household size for every household")
    size = size.astype(int).to_numpy()
    ages = households[list(HOUSEHOLD_AGE_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    valid = ages.ge(_VALID_AGE_RANGE[0]) & ages.le(_VALID_AGE_RANGE[1])
    n_valid = valid.sum(axis=1).to_numpy()
    too_many = n_valid > size
    if too_many.any():
        raise ValueError(
            f"[mid_donor] {int(too_many.sum())} households list more valid HP_ALTER ages than "
            f"H_GR members (e.g. H_ID {list(households['H_ID'][too_many][:5])}); check the "
            "household file.")
    known = pd.DataFrame({
        "household_id": np.repeat(households["H_ID"].to_numpy(), len(HOUSEHOLD_AGE_COLUMNS)),
        "age": ages.where(valid).to_numpy().ravel()}).dropna()
    n_unknown = size - n_valid
    unknown = pd.DataFrame({
        "household_id": np.repeat(households["H_ID"].to_numpy(), n_unknown),
        "age": float(_AGE_OF_MEMBER_WITHOUT_VALID_AGE)})
    long = pd.concat([known, unknown], ignore_index=True)
    n_members, n_fallback = int(size.sum()), int(n_unknown.sum())
    if n_members and n_fallback / n_members > age_unknown_raise_share:
        raise ValueError(
            f"[mid_donor] {n_fallback}/{n_members} household members "
            f"({100.0 * n_fallback / n_members:.1f}%) have no valid age (HP_ALTER outside "
            f"{_VALID_AGE_RANGE[0]}..{_VALID_AGE_RANGE[1]}), above "
            f"{100.0 * age_unknown_raise_share:.0f}%: a broken join with the household age "
            "file (check H_ID), not a few non-responses.")
    _log.info(
        "[mid_donor] consumption units: member age primary %d/%d (%.1f%%), fallback %d/%d (%.1f%%) "
        "counted as 14 or older", n_members - n_fallback, n_members,
        100.0 * (n_members - n_fallback) / max(n_members, 1), n_fallback, n_members,
        100.0 * n_fallback / max(n_members, 1))
    units = calculate_consumption_units(long)
    return units.set_index("household_id")["consumption_units"]


def attach_donor_household(
        persons, households, rng, age_unknown_raise_share=HOUSEHOLD_AGE_UNKNOWN_RAISE_SHARE):
    """Add each donor's MiD household income (EUR), economic status, size and consumption units
    (ADR-0191).

    ``household_size`` is ``H_GR`` (6 = six and more) and ``consumption_units`` the OECD-modified
    scale from the interview ages ``HP_ALTER_1..6`` (:func:`_household_consumption_units`).

    ``economic_status`` is the household's ``oek_status`` and the income bracket its
    ``hheink_gr1`` group, both mapped with the resident popsim mappers
    (``attributes.map_economic_status`` / ``map_household_income``): item non-response is
    imputed from the valid pool of the same household-size group (and RegioStaR7 when the
    frame has it), its rate logged by ``missing.resolve``. ``household_income_eur`` is a
    continuous draw WITHIN that bracket (``placement_income.draw_own_income_eur``: uniform,
    truncated Pareto in the open top bracket), the same within-bracket ASSUMPTION as for
    residents (ADR-0069). One value per donor household, shared by its members.

    Args:
        persons: donor persons of :func:`build_mid_donor_frames` (``person_id`` = ``H_ID_P_ID``).
        households: raw MiD households with :data:`DONOR_HOUSEHOLD_INCOME_COLUMNS`.
        rng: ``numpy.random.RandomState`` of this draw (its own stream, see
            :data:`DONOR_HOUSEHOLD_INCOME_RNG_OFFSET`).

    Returns:
        A copy of ``persons`` with ``household_income_eur`` (EUR/month) and ``economic_status``;
        both NaN for a person without a household record (counted and warned).

    Raises:
        ValueError: if a column is missing, or more than
            :data:`DONOR_HOUSEHOLD_MISSING_RAISE_SHARE` of the persons have no household record.
    """
    import pandas as pd

    from braunschweig.popsim import attributes, placement_income

    missing_cols = [c for c in DONOR_HOUSEHOLD_INCOME_COLUMNS if c not in households.columns]
    if missing_cols:
        raise ValueError(
            f"[mid_donor] the MiD household table lacks {missing_cols}; the in-commuter household "
            f"income and economic status are read from it (ADR-0191).")
    duplicated = households["H_ID"][households["H_ID"].duplicated()].unique()
    if len(duplicated):
        raise ValueError(
            f"[mid_donor] the MiD household table repeats {len(duplicated)} H_ID values "
            f"(e.g. {list(duplicated[:5])}); one household income per H_ID is required.")
    hh = attributes.map_economic_status(households, rng=rng)
    hh = attributes.map_household_income(hh, rng=rng)
    hh["household_income_eur"] = placement_income.draw_own_income_eur(hh["household_income"], rng)
    hh["household_size"] = pd.to_numeric(hh["H_GR"], errors="coerce")
    units = _household_consumption_units(households, age_unknown_raise_share)
    hh["consumption_units"] = hh["H_ID"].map(units).to_numpy()
    by_household = hh.assign(H_ID=hh["H_ID"].astype(str)).set_index("H_ID")
    household_of_person = persons["person_id"].astype(str).str.split("_").str[0]
    out = persons.copy()
    for column in ("household_income_eur", "economic_status", "household_size",
                   "consumption_units"):
        out[column] = household_of_person.map(by_household[column]).to_numpy()
    if out["household_size"].notna().all():
        out["household_size"] = out["household_size"].astype(int)
    # A person whose H_ID has no row in the household table (the raw B1 files carry a few) has
    # no household income; it stays NaN here and the worker in-commuter stage leaves such
    # donors out of its pool (incommuters.build_incommuter_frames), counted in both places.
    n = len(out)
    n_missing = int(out[["household_income_eur", "economic_status", "household_size",
                         "consumption_units"]].isna().any(axis=1).sum())
    share = n_missing / n if n else 0.0
    message = (
        f"[mid_donor] donor household income: primary {n - n_missing}/{n} donors "
        f"({100.0 * (1.0 - share):.2f}%) from their own MiD household, {n_missing} "
        f"({100.0 * share:.2f}%) without a household record (income and status NaN)")
    if share > DONOR_HOUSEHOLD_MISSING_RAISE_SHARE:
        raise ValueError(
            f"{message}; above {100.0 * DONOR_HOUSEHOLD_MISSING_RAISE_SHARE:.0f}% this is a broken "
            f"person-household join, not a few orphan persons (check the H_ID of both tables).")
    if n_missing:
        _log.warning(message)
    else:
        _log.info(message)
    return out


def attach_donor_draw_attributes(persons, raw_persons, trips):
    """Add the attributes the in-commuter donor draw reads (design: incommuter donor household).

    Adds ``donor_weight`` (``P_GEW``), ``weekday_reporting_day`` (``kernwo`` in
    ``braunschweig.popsim.trips.WEEKDAY_DIARY_KERNWO``), both joined from ``raw_persons`` on
    ``person_id = H_ID_P_ID``, and ``commute_distance_km``: ``wegkm_imp`` of the donor's first
    leg (in ``trip_id`` order) between home and work in either direction, the rule of
    ``braunschweig.popsim.commute_distance``; NaN for a donor without such a leg (counted).

    Raises:
        ValueError: naming a missing input column, or an impossible ``wegkm_imp`` value.
    """
    import pandas as pd

    from braunschweig.popsim.diary_facts import validate_trip_length_km
    from braunschweig.popsim.trips import WEEKDAY_DIARY_KERNWO

    for name, frame, columns in (
            ("raw persons", raw_persons, ("H_ID", "P_ID", "P_GEW", "kernwo")),
            ("trips", trips, ("person_id", "trip_id", "preceding_purpose", "following_purpose",
                              "wegkm_imp"))):
        missing = [c for c in columns if c not in frame.columns]
        if missing:
            raise ValueError(f"[mid_donor] the {name} table lacks {missing}; the donor draw "
                             "attributes are read from it.")
    raw = raw_persons.assign(
        person_id=raw_persons["H_ID"].astype(str) + "_" + raw_persons["P_ID"].astype(str))
    if not raw["person_id"].is_unique:
        raise ValueError("[mid_donor] non-unique (H_ID, P_ID) in the raw persons")
    raw = raw.set_index("person_id")
    out = persons.copy()
    out["donor_weight"] = out["person_id"].map(
        pd.to_numeric(raw["P_GEW"], errors="coerce")).to_numpy(dtype=float)
    weekday = pd.to_numeric(raw["kernwo"], errors="coerce").isin(WEEKDAY_DIARY_KERNWO)
    out["weekday_reporting_day"] = out["person_id"].map(weekday).fillna(False).astype(bool).to_numpy()

    is_commute = (
        ((trips["preceding_purpose"] == "home") & (trips["following_purpose"] == "work"))
        | ((trips["preceding_purpose"] == "work") & (trips["following_purpose"] == "home")))
    legs = trips[is_commute].sort_values(["person_id", "trip_id"], kind="stable")
    legs = legs.drop_duplicates("person_id", keep="first")
    km = validate_trip_length_km(legs["wegkm_imp"], log_tag="mid_donor")
    out["commute_distance_km"] = out["person_id"].map(
        pd.Series(km.to_numpy(), index=legs["person_id"].to_numpy())).to_numpy(dtype=float)
    n = len(out)
    n_weekday = int(out["weekday_reporting_day"].sum())
    kernwo_by_person = pd.to_numeric(raw["kernwo"], errors="coerce")
    n_unreadable = int(out["person_id"].map(kernwo_by_person).isna().sum())
    n_weekend = n - n_weekday - n_unreadable
    n_commute = int(out["commute_distance_km"].notna().sum())
    _log.info(
        "[mid_donor] donor draw attributes: reporting day weekday %d/%d (%.1f%%), weekend %d/%d, "
        "unreadable kernwo %d/%d; commute distance from a home-work leg primary %d/%d (%.1f%%), "
        "fallback (no such leg, NaN) %d/%d",
        n_weekday, n, 100.0 * n_weekday / max(n, 1), n_weekend, n, n_unreadable, n,
        n_commute, n, 100.0 * n_commute / max(n, 1), n - n_commute, n)
    return out


def configure(context):
    # Any cordon-enabled config without an explicit mid_raw_path falls back to
    # the canonical committed MiD path, so enabling cordon never hard-fails at
    # graph-build; a config that needs a different MiD dir sets the key
    # explicitly (as the popsim configs do).
    context.config(KEY_MID, DEFAULT_MID_RAW_PATH)
    context.config("random_seed")
    context.config(KEY_DEMOGRAPHICS, True)


def execute(context):
    """synpp stage entry point: load the raw MiD donor tables and build the
    in-commuter HTS donor frames.

    Consumes ``mid_raw_path`` (KEY_MID) verbatim, matching the sibling popsim
    stages (braunschweig.popsim.stage, braunschweig.popsim.completed_donor);
    falls back to ``DEFAULT_MID_RAW_PATH`` when the config does not set the
    key explicitly (see ``configure``/``DEFAULT_MID_RAW_PATH``). The
    configured path is repo-root-relative (e.g.
    "eqasim-data/data/braunschweig/popsim/mid2023_raw").
    """
    from braunschweig.popsim.sources.mid import MidSource

    # configure() declares KEY_MID with DEFAULT_MID_RAW_PATH as its default, so the
    # single-argument execute-time read returns that default when a cordon-enabled
    # config does not set mid_raw_path explicitly. (ExecuteContext.config() takes the
    # key alone; passing a default here would crash the stage at runtime.)
    mid_dir = context.config(KEY_MID)
    households, persons, wege = MidSource().load_donor(mid_dir)
    random_seed = int(context.config("random_seed"))
    households_out, persons_out, trips = build_mid_donor_frames(
        households, persons, wege, np.random.RandomState(random_seed),
        preserve_demographics=context.config(KEY_DEMOGRAPHICS))
    households_with_ages = households.merge(
        _load_household_ages(mid_dir), on="H_ID", how="left", validate="one_to_one")
    persons_out = attach_donor_household(
        persons_out, households_with_ages,
        np.random.RandomState(random_seed + DONOR_HOUSEHOLD_INCOME_RNG_OFFSET))
    persons_out = attach_donor_draw_attributes(persons_out, persons, trips)
    return households_out, persons_out, trips
