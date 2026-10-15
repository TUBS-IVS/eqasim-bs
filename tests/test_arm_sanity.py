"""The per-arm sanity check of the parking cost zones v3 comparison runs (``scripts/parking/arm_sanity.py``, issue #436, Part C).

The script reads one arm's resolved config, its pipeline log (and optionally the MATSim log) and the outcome report of one
iteration, and answers whether the arm may be compared at all: module active, config echo, source hashes, EV exemption,
NO_FEASIBLE_OPTION rate per zone group and (report only) the realised free share. Everything is synthetic: a log and a
report written from the arm's config, one defect injected per test. The lines the checks key on are produced by the production
functions where they are cheap to call (the EV exemption attach and the run check of ``braunschweig.parking``), and a drift
guard pins the remaining format fragments to the source that writes them, so a reworded production log line fails here instead
of failing silently on the server.
"""
from __future__ import annotations

import importlib.util
import json
import gzip
import logging
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "parking" / "arm_sanity.py"

V4_HEADER = ("outcome,zone_id,purpose,count,share,garage_probability_sum,stays_with_garages_in_range,"
             "garage_probability_sum_paid,garage_probability_sum_free,street_infeasible_count,no_feasible_option_count,"
             "expected_cost_cents_sum,ev_exempt_count")
V3_HEADER = V4_HEADER.rsplit(",", 6)[0]

SOURCES = {"parking_zones_2026": "7b200b87abcd", "parking_resident_districts_2026": "5cc8d31d1234",
           "parking_garages_2026": "84cf09f60000"}
LOGGED_SOURCES = {"parking_zones_2026": "7b200b87abcd", "parking_tariffs_2026": "aaaaaaaaaaaa",
                  "parking_coverage_register_2026": "bbbbbbbbbbbb", "srv2023_commute_parking_by_workplace_class": "cccccccccccc",
                  "parking_resident_districts_2026": "5cc8d31d1234", "parking_garages_2026": "84cf09f60000"}

#: The arm of the tests: the v3 arm of the plan with every pinned value of the committed base config.
BASE_CONFIG = {
    "parking_zones_enabled": True,
    "parking_tariff_snapshot_date": "2026-10-14",
    "parking_garage_decay_m": 575.27,
    "parking_garage_decay_commute_m": 69.69,
    "parking_option_price_beta_u_per_eur": 0.310998,
    "parking_cost_utility_factor": 1.0,
    "parking_ev_exemption_enabled": True,
    "parking_workplace_free_share_shift": 0.0,
}


@pytest.fixture(scope="module")
def sanity():
    spec = importlib.util.spec_from_file_location("arm_sanity_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------------------------------------------ synthetic inputs


def write_config(path: Path, **overrides) -> Path:
    """The resolved config (``<working_directory>/.merged_config.yml``: a ``config:`` section); a value of ``None`` drops a key."""
    config = {**BASE_CONFIG, **overrides}
    lines = ["run:", "  - matsim.output", "", "config:", "  sampling_rate: 0.01"]
    for key, value in config.items():
        if value is None:
            continue
        text = "true" if value is True else "false" if value is False else json.dumps(value)
        lines.append(f"  {key}: {text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def pipeline_lines(*, snapshot="2026-10-14", cost_factor="1", beta="0.310998", decay="575.27", commute="69.69",
                   sources=None, ev="on", run_check=True) -> list[str]:
    """The pipeline log of a sane v3 arm, in the formats of the production writers (see the drift guard below)."""
    sources = LOGGED_SOURCES if sources is None else sources
    lines = [
        "2026-10-14 08:00:00 INFO [parking] zone release: 12 zones (street_paid 10, campus 2) in 3 municipalities; coverage "
        "register 40 rows (covered 40); free shares for 6 workplace classes; resident districts 3 (03101 3); garages 5 "
        "(priced 5); sources " + ", ".join(f"{name} {prefix}" for name, prefix in sources.items()),
        f"[parking] prepared inputs: 12 zones (campus 2, street_paid 10) and 3 resident districts, terminal stay rule "
        f"until_fee_end, minimum stay 15 min, cost factor {cost_factor}, option price coefficient {beta} per EUR; garages "
        f"priced 5 of 5 listed, decay length {decay} m (garage options ON), commuter decay length {commute} m (commuter "
        f"garage options ON), maximum distance 1000.0 m; tariff model braunschweig_1pct_parking_tariffs_{snapshot}.json, "
        "module braunschweigParking in braunschweig_1pct_config.xml, report braunschweig_1pct_parking_inputs_report.json",
    ]
    if ev == "on":
        lines += [
            "2026-10-14 08:01:00 INFO [parking] EV exemption: 1200 of 5000 car owners (24.0 %) are BEV/PHEV and exempt where "
            "a zone grants it (incl. placeholder-car owners)",
            "2026-10-14 08:01:00 INFO [parking] EV exemption basis (ASSUMPTION E1): fleet-car owners 4000 of 5000 (80.0 %), "
            "the others own only the placeholder routing car; of the 5000 car owners: BEV/PHEV 1200 (24.0 % of car owners, "
            "30.0 % of fleet-car owners), other known powertrain 2800 (56.0 %), placeholder car without powertrain 1000 "
            "(20.0 %), car without powertrain 0 (0.0 %); several cars 0, of them mixed electric/non-electric 0 (exempt: any "
            "owned car is a BEV/PHEV); powertrains of the car vehicles: ice 2800, bev 1000, phev 200",
        ]
    elif ev == "off":
        lines.append("2026-10-14 08:01:00 INFO [parking] EV exemption is disabled (parking_ev_exemption_enabled false): no "
                     "parkingEvExempt attribute is written")
    if run_check:
        lines.append("2026-10-14 09:30:00 INFO [parking] run check: jar contains the parking package; outcomes written for "
                     "iteration 60")
    return lines


def java_lines(*, cost_factor="1.0", beta="0.310998", warning=None) -> list[str]:
    """The Java startup lines of a sane arm (ParkingOutcomeReportListener.notifyStartup, ParkingOptionPriceBetaCheck)."""
    lines = [
        "INFO ParkingOutcomeReportListener [parking] minimum stay 15 min (braunschweigParking.minimumStayMinutes, assumption "
        "L1): every priced car stay lasts at least 900 s; the simulated timing is unchanged",
        "INFO ParkingOutcomeReportListener [parking] search time (lever 4): off (searchTimeFactor 0); the car utility "
        "carries no parking search time term (version 1)",
        f"INFO ParkingOutcomeReportListener [parking] parking cost factor {cost_factor} (braunschweigParking.costFactor, "
        "assumption CF): the parking cost of a car trip enters the car cost unscaled (the model value)",
        f"INFO ParkingOptionPriceBetaCheck [parking] option price coefficient {beta} per EUR equals the magnitude of the mode "
        f"choice cost coefficient betaCost_u_MU ({beta}, tolerance 1.0E-9)",
    ]
    if warning:
        lines.append(warning)
    return lines


def write_lines(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_report(path: Path, rows, *, header: str = V4_HEADER) -> Path:
    """An outcome report v4 of ``rows`` = (outcome, zone id, purpose, count); every other column follows the Java rules."""
    total = sum(row[3] for row in rows)
    lines = [header]
    for outcome, zone_id, purpose, count in rows:
        line = f"{outcome},{zone_id},{purpose},{count},{count / total:.6f},0.000000,0"
        line += (f",0.000000,0.000000,0,{count if outcome == 'NO_FEASIBLE_OPTION' else 0},0.000000,"
                 f"{count if outcome == 'EV_EXEMPT' else 0}")
        lines.append(line if header == V4_HEADER else line.rsplit(",", 6)[0])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


#: A sane report: Ia 100 priced stays with 1 infeasible (exactly 1 %, the boundary), Ib 200 with 0, bh/se/wob areas high.
SANE_ROWS = [
    ("PAID_METERED", "bs_zone_ia", "shop", 99),
    ("NO_FEASIBLE_OPTION", "bs_zone_ia", "shop", 1),
    ("PAID_METERED", "bs_zone_ib", "shop", 200),
    ("PAID_METERED", "bh_kurpark", "leisure", 10),
    ("NO_FEASIBLE_OPTION", "bh_kurpark", "leisure", 10),
    ("PAID_METERED", "se_am_markt", "shop", 10),
    ("NO_FEASIBLE_OPTION", "se_am_markt", "shop", 30),
    ("PAID_METERED", "wob_area_a1", "shop", 5),
    ("NO_FEASIBLE_OPTION", "wob_area_a1", "shop", 5),
    ("PAID_METERED", "wob_tarifzone_1", "shop", 4),
    ("NO_FEASIBLE_OPTION", "wob_tarifzone_1", "shop", 4),
    ("NO_ZONE", "", "shop", 500),
]


class Arm:
    """The files of one synthetic arm in ``tmp_path``; ``run`` calls the script's ``main`` and returns (code, output)."""

    def __init__(self, tmp_path: Path, sanity, capsys):
        self.dir, self.sanity, self.capsys = tmp_path, sanity, capsys
        self.config = write_config(tmp_path / "arm_config.yml")
        self.log = write_lines(tmp_path / "pipeline.log", pipeline_lines())
        self.java_log = write_lines(tmp_path / "matsim.log", java_lines())
        self.report = write_report(tmp_path / "60.parking_outcomes.csv", SANE_ROWS)
        self.sources = tmp_path / "expected_sources.json"
        self.sources.write_text(json.dumps(SOURCES), encoding="utf-8")
        self.zone_groups = None
        self.inputs_report = None
        self.extra_logs = []

    def run(self, extra=()):
        arguments = ["--arm-config", str(self.config), "--log", str(self.log), "--java-log", str(self.java_log),
                     "--report", str(self.report), "--expected-sources", str(self.sources), *extra]
        for log in self.extra_logs:
            arguments += ["--log", str(log)]
        if self.zone_groups is not None:
            arguments += ["--zone-groups", str(self.zone_groups)]
        if self.inputs_report is not None:
            arguments += ["--inputs-report", str(self.inputs_report)]
        code = self.sanity.main(arguments)
        captured = self.capsys.readouterr()
        return code, captured.out, captured.err


@pytest.fixture
def arm(tmp_path, sanity, capsys):
    return Arm(tmp_path, sanity, capsys)


def result_of(output: str, check: str) -> str:
    """The output line of one check (lines start with ``[PASS|FAIL|INFO] <check>:``)."""
    prefixes = tuple(f"[{tag}] {check}:" for tag in ("PASS", "FAIL", "INFO"))
    lines = [line for line in output.splitlines() if line.startswith(prefixes)]
    assert len(lines) == 1, f"no single line for check {check!r} in: {output!r}"
    return lines[0]


# ------------------------------------------------------------------------------------------------------- a sane arm


def test_a_sane_arm_passes_every_check_and_prints_one_line_per_check(arm):
    code, out, err = arm.run()
    assert code == 0, out + err
    names = [line.split(" ", 2)[1].rstrip(":") for line in out.splitlines() if line.startswith("[")]
    assert names == ["module_active", "config_echo", "sources", "ev_exemption", "no_feasible_option", "free_share"]
    assert "FAIL" not in out
    assert result_of(out, "free_share").startswith("[INFO] ")    # report only, never a verdict


# ------------------------------------------------------------------------------------------------ 1: module active


def test_a_missing_run_check_line_fails(arm):
    write_lines(arm.log, pipeline_lines(run_check=False))
    code, out, _ = arm.run()
    assert code == 1
    line = result_of(out, "module_active")
    assert line.startswith("[FAIL]") and "[parking] run check" in line
    assert "FIRST FAILURE" in out and "module_active" in out.split("FIRST FAILURE", 1)[1]


def test_the_run_check_line_may_be_in_the_java_log_only_when_it_is_in_the_pipeline_log_not(arm):
    # The run check is written by the Python stage (matsim.simulation.run), so it is searched in the pipeline log.
    write_lines(arm.log, pipeline_lines(run_check=False))
    write_lines(arm.java_log, java_lines() + pipeline_lines()[-1:])
    code, out, _ = arm.run()
    assert code == 1 and result_of(out, "module_active").startswith("[FAIL]")


@pytest.mark.parametrize("header", [V3_HEADER,
                                    ",".join(V4_HEADER.split(",")[:-1]),
                                    ",".join(reversed(V4_HEADER.split(","))),
                                    V4_HEADER + ",extra"])
def test_a_report_header_that_is_not_exactly_v4_fails(arm, header):
    write_report(arm.report, [row for row in SANE_ROWS], header=header)
    code, out, _ = arm.run()
    assert code == 1
    line = result_of(out, "module_active")
    assert line.startswith("[FAIL]") and "header" in line
    # The checks that need the v4 columns say so instead of crashing or passing.
    assert result_of(out, "no_feasible_option").startswith("[FAIL]")


def test_the_header_of_a_report_in_another_column_order_is_not_accepted_as_v4(arm):
    columns = V4_HEADER.split(",")
    swapped = ",".join(columns[:3] + [columns[4], columns[3]] + columns[5:])
    write_report(arm.report, SANE_ROWS, header=swapped)
    code, out, _ = arm.run()
    assert code == 1 and result_of(out, "module_active").startswith("[FAIL]")


# -------------------------------------------------------------------------------------------------- 2: config echo


@pytest.mark.parametrize("override, expected_word", [
    ({"snapshot": "2026-10-13"}, "snapshot"),
    ({"decay": "324.31"}, "parking_garage_decay_m"),
    ({"commute": "261.57"}, "parking_garage_decay_commute_m"),
    ({"beta": "0"}, "parking_option_price_beta_u_per_eur"),
    ({"cost_factor": "0.5"}, "parking_cost_utility_factor"),
])
def test_a_logged_value_that_differs_from_the_arm_config_fails(arm, override, expected_word):
    write_lines(arm.log, pipeline_lines(**override))
    code, out, _ = arm.run()
    assert code == 1
    line = result_of(out, "config_echo")
    assert line.startswith("[FAIL]") and expected_word in line


def test_the_java_cost_factor_line_must_equal_the_config(arm):
    write_config(arm.config, parking_cost_utility_factor=0.5)
    write_lines(arm.log, pipeline_lines(cost_factor="0.5"))
    write_lines(arm.java_log, java_lines(cost_factor="1.0"))      # the jar ran with the default: the overlay never arrived
    code, out, _ = arm.run()
    assert code == 1
    line = result_of(out, "config_echo")
    assert line.startswith("[FAIL]") and "costFactor" in line and "1.0" in line and "0.5" in line


def test_a_cost_factor_arm_with_matching_java_and_python_lines_passes(arm):
    write_config(arm.config, parking_cost_utility_factor=2.0)
    write_lines(arm.log, pipeline_lines(cost_factor="2"))
    write_lines(arm.java_log, java_lines(cost_factor="2.0"))
    code, out, _ = arm.run()
    assert code == 0, out
    assert "2" in result_of(out, "config_echo")


def test_an_absent_cost_factor_in_the_config_means_1_0(arm):
    write_config(arm.config, parking_cost_utility_factor=None)
    code, out, _ = arm.run()
    assert code == 0, out
    write_lines(arm.java_log, java_lines(cost_factor="0.5"))
    code, out, _ = arm.run()
    assert code == 1 and "costFactor" in result_of(out, "config_echo")


def test_a_missing_java_cost_factor_line_fails_instead_of_passing_silently(arm):
    write_lines(arm.java_log, [line for line in java_lines() if "cost factor" not in line])
    code, out, _ = arm.run()
    assert code == 1
    assert result_of(out, "config_echo").startswith("[FAIL]") and "costFactor" in result_of(out, "config_echo")


def test_the_java_beta_line_must_equal_the_config_beta(arm):
    write_lines(arm.java_log, java_lines(beta="0.5"))
    code, out, _ = arm.run()
    assert code == 1 and "option price coefficient" in result_of(out, "config_echo")


def test_without_a_java_log_the_java_lines_are_read_from_the_pipeline_log(arm):
    write_lines(arm.log, pipeline_lines() + java_lines())
    arm.java_log = None
    arguments = ["--arm-config", str(arm.config), "--log", str(arm.log), "--report", str(arm.report),
                 "--expected-sources", str(arm.sources)]
    assert arm.sanity.main(arguments) == 0
    write_lines(arm.log, pipeline_lines())                 # no Java line anywhere
    assert arm.sanity.main(arguments) == 1


def test_a_config_without_a_pinned_key_is_an_input_error_not_a_default(arm):
    write_config(arm.config, parking_option_price_beta_u_per_eur=None)
    code, out, err = arm.run()
    assert code == 2 and "parking_option_price_beta_u_per_eur" in err and out == ""


def test_a_config_may_be_the_flat_mapping_without_a_config_section(arm, tmp_path):
    flat = tmp_path / "flat.yml"
    flat.write_text("\n".join(f"{key}: {json.dumps(value)}" for key, value in BASE_CONFIG.items()) + "\n", encoding="utf-8")
    arm.config = flat
    code, out, _ = arm.run()
    assert code == 0, out


# ------------------------------------------------------------------------------------------------------- 3: sources


def test_logged_prefixes_equal_the_expected_sources(arm):
    code, out, _ = arm.run()
    line = result_of(out, "sources")
    assert code == 0 and line.startswith("[PASS]") and "7b200b87abcd" in line


def test_a_logged_prefix_that_differs_fails(arm):
    changed = {**LOGGED_SOURCES, "parking_garages_2026": "0123456789ab"}
    write_lines(arm.log, pipeline_lines(sources=changed))
    code, out, _ = arm.run()
    line = result_of(out, "sources")
    assert code == 1 and line.startswith("[FAIL]")
    assert "parking_garages_2026" in line and "0123456789ab" in line and "84cf09f60000" in line


def test_an_expected_dataset_missing_from_the_log_fails(arm):
    reduced = {name: prefix for name, prefix in LOGGED_SOURCES.items() if name != "parking_garages_2026"}
    write_lines(arm.log, pipeline_lines(sources=reduced))
    code, out, _ = arm.run()
    assert code == 1 and "parking_garages_2026" in result_of(out, "sources")


def test_a_missing_release_line_fails(arm):
    write_lines(arm.log, pipeline_lines()[1:])
    code, out, _ = arm.run()
    assert code == 1 and "zone release" in result_of(out, "sources")


def test_the_install_list_may_name_fewer_datasets_than_the_release_logs(arm):
    arm.sources.write_text(json.dumps({"parking_zones_2026": "7b200b87abcd"}), encoding="utf-8")
    code, out, _ = arm.run()
    assert code == 0, out


@pytest.mark.parametrize("bad", [{"parking_zones_2026": "7b200b87"}, {"parking_zones_2026": "7B200B87ABCD"},
                                 {"parking_zones_2026": 7}, ["parking_zones_2026"], {}])
def test_an_expected_sources_file_that_is_not_a_map_of_12_char_prefixes_is_an_input_error(arm, bad):
    arm.sources.write_text(json.dumps(bad), encoding="utf-8")
    code, out, err = arm.run()
    assert code == 2 and "expected" in err.lower() and out == ""


# ----------------------------------------------------------------------------------------------------------- 4: EV


def test_ev_on_needs_exempt_persons_and_the_basis_line(arm):
    code, out, _ = arm.run()
    line = result_of(out, "ev_exemption")
    assert code == 0 and line.startswith("[PASS]") and "1200" in line and "powertrain" in line


def test_ev_on_with_zero_exempt_persons_fails(arm):
    lines = [line.replace("EV exemption: 1200 of 5000", "EV exemption: 0 of 5000") for line in pipeline_lines()]
    write_lines(arm.log, lines)
    code, out, _ = arm.run()
    assert code == 1 and result_of(out, "ev_exemption").startswith("[FAIL]")


def test_ev_on_without_the_basis_line_fails(arm):
    write_lines(arm.log, [line for line in pipeline_lines() if "EV exemption basis" not in line])
    code, out, _ = arm.run()
    assert code == 1 and "basis" in result_of(out, "ev_exemption")


def test_ev_on_with_the_missing_powertrain_column_warning_fails(arm):
    warning = ("[parking] EV exemption: the vehicles frame has no 'powertrain' column (the legacy fleet of "
               "fleet_model_enabled false carries none), so none of the 5000 car owners can be classified and nobody is exempt")
    write_lines(arm.log, pipeline_lines() + [warning])
    code, out, _ = arm.run()
    assert code == 1 and "powertrain" in result_of(out, "ev_exemption")


def test_ev_on_in_the_config_but_the_disabled_line_in_the_log_fails(arm):
    write_lines(arm.log, pipeline_lines(ev="off"))
    code, out, _ = arm.run()
    assert code == 1 and result_of(out, "ev_exemption").startswith("[FAIL]")


def test_ev_on_in_the_config_without_any_ev_line_fails(arm):
    write_lines(arm.log, pipeline_lines(ev="none"))
    code, out, _ = arm.run()
    assert code == 1 and result_of(out, "ev_exemption").startswith("[FAIL]")


def test_ev_off_needs_the_disabled_line(arm):
    write_config(arm.config, parking_ev_exemption_enabled=False)
    write_lines(arm.log, pipeline_lines(ev="off"))
    code, out, _ = arm.run()
    line = result_of(out, "ev_exemption")
    assert code == 0 and line.startswith("[PASS]") and "disabled" in line


def test_ev_off_with_exempt_persons_in_the_log_fails(arm):
    write_config(arm.config, parking_ev_exemption_enabled=False)
    code, out, _ = arm.run()                            # the log of an EV-on arm: the overlay did not arrive
    assert code == 1 and result_of(out, "ev_exemption").startswith("[FAIL]")


def test_ev_off_without_the_disabled_line_fails(arm):
    write_config(arm.config, parking_ev_exemption_enabled=False)
    write_lines(arm.log, pipeline_lines(ev="none"))
    code, out, _ = arm.run()
    assert code == 1 and result_of(out, "ev_exemption").startswith("[FAIL]")


def test_the_flag_in_the_config_must_be_a_boolean(arm):
    write_config(arm.config, parking_ev_exemption_enabled="yes")
    code, out, err = arm.run()
    assert code == 2 and "parking_ev_exemption_enabled" in err


# -------------------------------------------------------------------------------------- 5: NO_FEASIBLE_OPTION per group


def test_one_percent_in_ia_is_the_boundary_and_passes(arm):
    # SANE_ROWS: Ia has 100 priced stays, 1 of them NO_FEASIBLE_OPTION = exactly 1 % (the rule is "above 1 %").
    code, out, _ = arm.run()
    line = result_of(out, "no_feasible_option")
    assert code == 0 and line.startswith("[PASS]") and "bs_zone_ia 1/100 (1.00 %)" in line


def test_above_one_percent_in_ia_fails(arm):
    rows = [("PAID_METERED", "bs_zone_ia", "shop", 98), ("NO_FEASIBLE_OPTION", "bs_zone_ia", "shop", 2)] + SANE_ROWS[2:]
    write_report(arm.report, rows)
    code, out, _ = arm.run()
    line = result_of(out, "no_feasible_option")
    assert code == 1 and line.startswith("[FAIL]") and "bs_zone_ia 2/100 (2.00 %)" in line
    assert "FIRST FAILURE" in out


def test_above_one_percent_in_ib_fails_even_when_ia_is_clean(arm):
    rows = SANE_ROWS[:2] + [("PAID_METERED", "bs_zone_ib", "shop", 150), ("NO_FEASIBLE_OPTION", "bs_zone_ib", "shop", 3)]
    write_report(arm.report, rows + SANE_ROWS[3:])
    code, out, _ = arm.run()
    assert code == 1 and "bs_zone_ib 3/153" in result_of(out, "no_feasible_option")


def test_ia_and_ib_are_judged_separately(arm):
    # Pooled the rate would be 3/203 = 1.5 % and fail; the test of the zone pair alone: Ia 0 %, Ib 1 of 100 = 1 % passes.
    rows = [("PAID_METERED", "bs_zone_ia", "shop", 100), ("PAID_METERED", "bs_zone_ib", "shop", 99),
            ("NO_FEASIBLE_OPTION", "bs_zone_ib", "shop", 1)]
    write_report(arm.report, rows)
    code, out, _ = arm.run()
    assert code == 0, out


def test_other_groups_are_reported_and_never_fail(arm):
    # bh, se_am_markt and the Wolfsburg areas sit far above 1 % in SANE_ROWS (50 %, 75 %, 50 %, 50 %).
    code, out, _ = arm.run()
    line = result_of(out, "no_feasible_option")
    assert code == 0
    for group, share in (("bh", "10/20 (50.00 %)"), ("se_am_markt", "30/40 (75.00 %)"),
                         ("wob_area", "5/10 (50.00 %)"), ("other", "4/8 (50.00 %)")):
        assert f"{group} {share}" in line, line


def test_no_zone_stays_are_not_priced_stays(arm):
    # 500 NO_ZONE calls must not dilute the Ia rate: 2 of 98+2 priced stays fails although 2 of 600 would pass.
    rows = [("PAID_METERED", "bs_zone_ia", "shop", 98), ("NO_FEASIBLE_OPTION", "bs_zone_ia", "shop", 2),
            ("PAID_METERED", "bs_zone_ib", "shop", 10), ("NO_ZONE", "", "shop", 500)]
    write_report(arm.report, rows)
    code, out, _ = arm.run()
    assert code == 1 and "bs_zone_ia 2/100" in result_of(out, "no_feasible_option")


def test_a_report_without_priced_stays_in_ia_or_ib_cannot_be_judged_and_fails(arm):
    write_report(arm.report, [("PAID_METERED", "bs_zone_ia", "shop", 100), ("NO_ZONE", "", "shop", 5)])
    code, out, _ = arm.run()
    line = result_of(out, "no_feasible_option")
    assert code == 1 and line.startswith("[FAIL]") and "bs_zone_ib" in line and "no priced stay" in line


def test_a_custom_zone_group_file_maps_patterns_to_groups(arm, tmp_path):
    groups = tmp_path / "groups.yml"
    groups.write_text("bs_zone_ia: bs_zone_ia\nbs_zone_ib: bs_zone_ib\n'bh_*': spa\n'*': rest\n", encoding="utf-8")
    arm.zone_groups = groups
    code, out, _ = arm.run()
    line = result_of(out, "no_feasible_option")
    assert code == 0 and "spa 10/20 (50.00 %)" in line and "rest" in line and "bh " not in line


def test_a_zone_group_file_that_lacks_the_two_hard_stop_groups_is_an_input_error(arm, tmp_path):
    groups = tmp_path / "groups.json"
    groups.write_text(json.dumps({"bs_zone_*": "city", "*": "rest"}), encoding="utf-8")
    arm.zone_groups = groups
    code, out, err = arm.run()
    assert code == 2 and "bs_zone_ia" in err


def test_the_java_warning_of_the_last_iteration_is_quoted_as_evidence(arm):
    warning = ("[parking] it 60: 12 of 1000 priced stays (1.2 %) have no feasible parking option (NO_FEASIBLE_OPTION: the "
               "stay exceeds the maximum stay of the street and of every garage within reach) and are priced as the street "
               "at its metered price; the option set of these zones is incomplete (spec v3 A1, ASSUMPTION M3)")
    write_lines(arm.java_log, java_lines(warning=warning))
    code, out, _ = arm.run()
    assert code == 0 and "it 60: 12 of 1000" in result_of(out, "no_feasible_option")


# ------------------------------------------------------------------------------------------------ 6: free share (info)


FREE_ROWS = [
    ("EMPLOYER_FREE", "bs_zone_ia", "work", 30),
    ("PAID_METERED", "bs_zone_ia", "work", 50),
    ("EMPLOYER_FREE", "bs_zone_ib", "education", 10),
    ("PAID_COMMUTER", "bs_zone_ib", "education", 10),
    ("OUTSIDE_FEE_HOURS", "bs_zone_ib", "work", 20),
    ("EMPLOYER_FREE", "bs_zone_ia", "shop", 40),            # another purpose: not a work or education stay
    ("PAID_METERED", "bs_zone_ib", "shop", 200),
    ("HOME", "bs_zone_ib", "home", 30),
    ("NO_ZONE", "", "work", 99),
]


def test_the_realised_free_share_is_reported_next_to_the_configured_shift(arm):
    write_config(arm.config, parking_workplace_free_share_shift=-0.1)
    write_report(arm.report, FREE_ROWS)
    code, out, _ = arm.run()
    line = result_of(out, "free_share")
    # work + education calls in zones: 30+50+10+10+20 = 120; EMPLOYER_FREE 40 (33.33 %); no-charge incl. outside fee hours 60.
    assert code == 0 and line.startswith("[INFO]")
    assert "shift -0.100" in line and "employer_free 40/120 (33.33 %)" in line and "no_charge 60/120 (50.00 %)" in line


def test_a_free_share_report_never_fails_the_arm(arm):
    write_config(arm.config, parking_workplace_free_share_shift=0.0)
    write_report(arm.report, [("PAID_METERED", "bs_zone_ia", "shop", 100), ("PAID_METERED", "bs_zone_ib", "shop", 100)])
    code, out, _ = arm.run()
    assert code == 0 and "no work or education" in result_of(out, "free_share")


def test_a_config_without_the_shift_key_is_an_input_error(arm):
    write_config(arm.config, parking_workplace_free_share_shift=None)
    code, out, err = arm.run()
    assert code == 2 and "parking_workplace_free_share_shift" in err


# ---------------------------------------------------------------------------------------------- the command line, inputs


@pytest.mark.parametrize("attribute", ["config", "log", "java_log", "report", "sources"])
def test_a_missing_input_file_is_an_input_error_that_names_it(arm, attribute):
    path = getattr(arm, attribute)
    path.unlink()
    code, out, err = arm.run()
    assert code == 2 and path.name in err and out == ""


def test_the_first_failing_check_is_named_with_its_evidence(arm):
    write_lines(arm.log, pipeline_lines(decay="324.31", run_check=False))
    code, out, _ = arm.run()
    assert code == 1
    summary = out.splitlines()[-1]
    # module_active comes before config_echo in the check order: it is the first failure.
    assert summary.startswith("FIRST FAILURE: module_active") and "run check" in summary


def test_the_script_does_not_copy_the_report_header(sanity):
    cmp = sys.modules[sanity.COMPARE_MODULE_NAME]
    assert sanity.REPORT_V4_COLUMNS is cmp.REPORT_V4_COLUMNS


# ------------------------------------------------------------- the real producers of the lines the checks key on


def test_the_real_ev_attach_lines_are_accepted_for_an_enabled_arm_and_a_disabled_arm(sanity, caplog):
    from braunschweig.parking import attach

    persons = pd.DataFrame({"person_id": ["p1", "p2", "p3"]})
    vehicles = pd.DataFrame({"owner_id": ["p1", "p2", "p3"], "mode": ["car"] * 3, "powertrain": ["bev", "ice", "phev"],
                             "type_id": ["x"] * 3})
    with caplog.at_level(logging.INFO):
        attach.attach_ev_exemption(persons, vehicles, True)
    enabled_log = [record.getMessage() for record in caplog.records]
    caplog.clear()
    with caplog.at_level(logging.INFO):
        attach.attach_ev_exemption(persons, vehicles, False)
    disabled_log = [record.getMessage() for record in caplog.records]
    enabled_logs, disabled_logs = [sanity.LogSource("enabled", enabled_log)], [sanity.LogSource("disabled", disabled_log)]
    assert sanity.check_ev_exemption(enabled_logs, True).ok
    assert not sanity.check_ev_exemption(enabled_logs, False).ok
    assert sanity.check_ev_exemption(disabled_logs, False).ok
    assert not sanity.check_ev_exemption(disabled_logs, True).ok
    # one BEV owner and one PHEV owner of three: the headline is 2 of 3 and the check quotes it
    assert "2 of 3" in sanity.check_ev_exemption(enabled_logs, True).evidence


def test_the_real_ev_attach_lines_of_a_fleet_without_powertrains_fail_an_enabled_arm(sanity, caplog):
    from braunschweig.parking import attach

    persons = pd.DataFrame({"person_id": ["p1", "p2"]})
    vehicles = pd.DataFrame({"owner_id": ["p1", "p2"], "mode": ["car", "car"], "type_id": ["x", "x"]})   # no powertrain
    with caplog.at_level(logging.INFO):
        attach.attach_ev_exemption(persons, vehicles, True)
    logs = [sanity.LogSource("fleet", [record.getMessage() for record in caplog.records])]
    assert not sanity.check_ev_exemption(logs, True).ok


def test_the_real_run_check_line_is_accepted(sanity, tmp_path, caplog):
    from braunschweig.parking import runtime_checks

    output = tmp_path / "simulation_output"
    report = output / "ITERS" / "it.3"
    report.mkdir(parents=True)
    (report / "3.parking_outcomes.csv").write_text(V4_HEADER + "\n", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        runtime_checks.require_parking_outcomes(output)
    messages = [record.getMessage() for record in caplog.records]
    csv_path = write_report(tmp_path / "3.parking_outcomes.csv", SANE_ROWS)
    assert sanity.check_module_active([sanity.LogSource("run", messages)], csv_path).ok


def test_the_log_fragments_the_checks_key_on_exist_in_the_production_source():
    """Drift guard: a reworded production line must fail here, not silently on the server."""
    fragments = {
        "braunschweig/parking/runtime_checks.py": [
            "[parking] run check: jar contains the parking package; outcomes written"],
        "braunschweig/parking/zones_stage.py": [
            "zone release: %d zones", "sources %s", "{source['source_id']} {source['sha256'][:12]}"],
        "braunschweig/parking/attach.py": [
            "EV exemption: %d of %d car owners", "EV exemption basis (ASSUMPTION E1)",
            "powertrains of the car vehicles: %s", "EV exemption is disabled", "has no 'powertrain' column",
            "free-parking draw (RandomState(random_seed %d + %d), shift %+.3f): %d/%d persons",
            "work/education activity in a street_paid or resident_zone zone, %d of them"],
        "braunschweig/matsim/simulation/prepare.py": [
            "[parking] prepared inputs:", "cost factor %g", "option price coefficient %g per EUR",
            "decay length %s m (%s)", "commuter decay length %s m (%s)", "tariff model %s"],
        "braunschweig/parking/tariff_export.py": ["{prefix}parking_tariffs_{check_snapshot_date(snapshot_date)}.json"],
    }
    for relative, needles in fragments.items():
        text = (REPO / relative).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{relative} no longer contains {needle!r}: update scripts/parking/arm_sanity.py"


# ====================================================================================== fix round 1 (review of C1)
# --------------------------------------------------------------------- I-1: cached stages leave no log lines behind


def write_inputs_report(path: Path, *, sources=None, cost_factor=1.0, beta=0.310998, decay=575.27, commute=69.69,
                        snapshot="2026-10-14") -> Path:
    """``<prefix>parking_inputs_report.json`` as ``_write_parking_inputs`` writes it (the keys the checks read)."""
    sources = LOGGED_SOURCES if sources is None else sources
    report = {"parking_input_files": [f"braunschweig_1pct_parking_tariffs_{snapshot}.json",
                                      "braunschweig_1pct_parking_inputs_report.json"],
              "zones": 12, "garage_decay_m": decay, "garage_decay_commute_m": commute,
              "option_price_beta_u_per_eur": beta, "parking_cost_utility_factor": cost_factor,
              "sources": [{"source_id": name, "path": f"braunschweig/parking/{name}", "sha256": prefix + "0" * 52}
                          for name, prefix in sources.items()]}
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def cached_pipeline_lines(**kwargs) -> list[str]:
    """The pipeline log of a run whose zones and prepare stages were cached: no zone release and no prepared inputs line."""
    return pipeline_lines(**kwargs)[2:]


def set_age(path: Path, seconds_ago: int) -> None:
    now = path.stat().st_mtime
    os.utime(path, (now - seconds_ago, now - seconds_ago))


def test_the_inputs_report_replaces_the_log_lines_of_cached_stages(arm, tmp_path):
    write_lines(arm.log, cached_pipeline_lines())
    arm.inputs_report = write_inputs_report(tmp_path / "braunschweig_1pct_parking_inputs_report.json")
    code, out, err = arm.run()
    assert code == 0, out + err
    assert "inputs report braunschweig_1pct_parking_inputs_report.json" in result_of(out, "config_echo")
    sources = result_of(out, "sources")
    assert "full SHA-256" in sources and "7b200b87abcd" in sources and "FALLBACK" not in sources


def test_without_an_inputs_report_the_cached_stage_lines_are_missing_and_the_checks_say_what_to_pass(arm):
    write_lines(arm.log, cached_pipeline_lines())
    code, out, _ = arm.run()
    assert code == 1
    assert "--inputs-report" in result_of(out, "config_echo") and result_of(out, "config_echo").startswith("[FAIL]")
    assert "--inputs-report" in result_of(out, "sources") and result_of(out, "sources").startswith("[FAIL]")


def test_the_log_fallback_is_named_in_the_output(arm):
    code, out, _ = arm.run()
    assert code == 0, out
    assert "FALLBACK" in result_of(out, "config_echo") and "pipeline.log" in result_of(out, "config_echo")
    assert "FALLBACK" in result_of(out, "sources")


@pytest.mark.parametrize("override, check, word", [
    ({"decay": 324.31}, "config_echo", "parking_garage_decay_m"),
    ({"commute": 261.57}, "config_echo", "parking_garage_decay_commute_m"),
    ({"beta": 0.0}, "config_echo", "parking_option_price_beta_u_per_eur"),
    ({"cost_factor": 0.5}, "config_echo", "parking_cost_utility_factor"),
    ({"snapshot": "2026-10-13"}, "config_echo", "snapshot"),
    ({"sources": {**LOGGED_SOURCES, "parking_garages_2026": "0123456789ab"}}, "sources", "parking_garages_2026"),
])
def test_an_inputs_report_that_differs_from_the_config_or_the_install_list_fails(arm, tmp_path, override, check, word):
    arm.inputs_report = write_inputs_report(tmp_path / "report.json", **override)
    code, out, _ = arm.run()
    line = result_of(out, check)
    assert code == 1 and line.startswith("[FAIL]") and word in line


@pytest.mark.parametrize("content", ["not json", "[]", json.dumps({"sources": []}),
                                     json.dumps({"parking_input_files": ["x"], "sources": [{"source_id": "a", "sha256": "b"}]})])
def test_an_unusable_inputs_report_is_an_input_error(arm, tmp_path, content):
    arm.inputs_report = tmp_path / "report.json"
    arm.inputs_report.write_text(content, encoding="utf-8")
    code, out, err = arm.run()
    assert code == 2 and "inputs report" in err and out == ""


def test_a_missing_inputs_report_is_an_input_error(arm, tmp_path):
    arm.inputs_report = tmp_path / "absent.json"
    code, out, err = arm.run()
    assert code == 2 and "absent.json" in err


def test_the_ev_lines_may_come_from_the_log_of_an_earlier_arm_that_executed_the_population_stage(arm, tmp_path):
    earlier = write_lines(tmp_path / "earlier_arm.log", pipeline_lines()[2:4])          # only the two EV lines
    write_lines(arm.log, pipeline_lines(ev="none"))
    arm.extra_logs = [earlier]
    code, out, _ = arm.run()
    line = result_of(out, "ev_exemption")
    assert code == 0, out
    assert "earlier_arm.log" in line and "1200 of 5000" in line


def test_logs_are_searched_newest_first_and_the_supplying_log_is_named(arm, tmp_path):
    older = write_lines(tmp_path / "older.log", [line.replace("1200 of 5000", "7 of 5000") for line in pipeline_lines()[2:4]])
    arm.extra_logs = [older]
    set_age(older, 600)
    code, out, _ = arm.run()
    assert code == 0 and "1200 of 5000" in result_of(out, "ev_exemption") and "pipeline.log" in result_of(out, "ev_exemption")
    set_age(arm.log, 1200)                                   # now the extra log is the newer one
    code, out, _ = arm.run()
    assert code == 0 and "7 of 5000" in result_of(out, "ev_exemption") and "older.log" in result_of(out, "ev_exemption")


def test_the_run_check_line_of_an_earlier_log_is_found_too_but_the_log_is_named(arm, tmp_path):
    earlier = write_lines(tmp_path / "earlier_arm.log", pipeline_lines()[-1:])
    write_lines(arm.log, pipeline_lines(run_check=False))
    arm.extra_logs = [earlier]
    code, out, _ = arm.run()
    assert code == 0 and "earlier_arm.log" in result_of(out, "module_active")


def test_the_ev_headline_and_the_warning_must_come_from_one_log(arm, tmp_path):
    # The headline of the supplying log is judged with the lines of THAT log: an older log with the missing-column warning
    # does not condemn a newer log whose attach was fine, and the newer log's pass is not borrowed by the older one.
    warning = ("[parking] EV exemption: the vehicles frame has no 'powertrain' column (the legacy fleet), so none of the 5000 "
               "car owners can be classified and nobody is exempt")
    older = write_lines(tmp_path / "older.log", pipeline_lines()[2:4] + [warning])
    arm.extra_logs = [older]
    set_age(older, 600)
    code, out, _ = arm.run()
    assert code == 0, out
    set_age(older, -600)                                     # the older log is now the newer one and is the supplier
    code, out, _ = arm.run()
    assert code == 1 and "powertrain" in result_of(out, "ev_exemption")


def test_load_logs_orders_by_modification_time_newest_first(sanity, tmp_path):
    first, second, third = (write_lines(tmp_path / f"{name}.log", [name]) for name in ("a", "b", "c"))
    set_age(first, 300)
    set_age(second, 100)
    set_age(third, 200)
    assert [Path(log.name).name for log in sanity.load_logs([first, second, third])] == ["b.log", "c.log", "a.log"]


# -------------------------------------------------------------------------------------------------------- M1: bad input


@pytest.mark.parametrize("content", [b"config: [unclosed", b"\xff\xfe\x00 not utf-8", b"{{{"])
def test_a_malformed_or_undecodable_config_is_an_input_error(arm, content):
    arm.config.write_bytes(content)
    code, out, err = arm.run()
    assert code == 2 and "arm config" in err and out == ""


def test_an_undecodable_or_empty_report_is_an_input_error(arm):
    arm.report.write_bytes(b"\xff\xfe\x00 binary")
    code, out, err = arm.run()
    assert code == 2 and "outcome report" in err and out == ""
    arm.report.write_bytes(b"")
    code, out, err = arm.run()
    assert code == 2 and "empty" in err and out == ""


def test_an_undecodable_expected_sources_file_and_zone_groups_file_are_input_errors(arm, tmp_path):
    arm.sources.write_bytes(b"\xff\xfe\x00")
    code, _, err = arm.run()
    assert code == 2 and "expected sources" in err
    arm.sources.write_text(json.dumps(SOURCES), encoding="utf-8")
    groups = tmp_path / "groups.yml"
    groups.write_text("a: [unclosed", encoding="utf-8")
    arm.zone_groups = groups
    code, _, err = arm.run()
    assert code == 2 and "zone-groups" in err


def test_the_v3_kind_needs_a_report_and_the_install_list(sanity, arm, capsys):
    base = ["--arm-config", str(arm.config), "--log", str(arm.log)]
    assert sanity.main(base + ["--expected-sources", str(arm.sources)]) == 2
    assert "--report" in capsys.readouterr().err
    assert sanity.main(base + ["--report", str(arm.report)]) == 2
    assert "--expected-sources" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ M2, M3, M4, M5, M6


def test_the_ev_pass_evidence_names_what_proves_the_powertrain_column(arm):
    code, out, _ = arm.run()
    line = result_of(out, "ev_exemption")
    assert code == 0 and "(exempt > 0)" in line and "no warning" in line and "powertrain column" in line
    assert "names the powertrain column" not in line          # the basis line is printed regardless: no evidence of it


def test_the_south_and_second_zone_of_braunschweig_are_reported_and_never_fail(arm):
    rows = SANE_ROWS + [("PAID_METERED", "bs_zone_ia_sued", "shop", 5), ("NO_FEASIBLE_OPTION", "bs_zone_ia_sued", "shop", 5),
                        ("PAID_METERED", "bs_zone_ii", "shop", 2), ("NO_FEASIBLE_OPTION", "bs_zone_ii", "shop", 8)]
    write_report(arm.report, rows)
    code, out, _ = arm.run()
    line = result_of(out, "no_feasible_option")
    assert code == 0, out
    assert "bs_zone_ia_sued 5/10 (50.00 %)" in line and "bs_zone_ii 8/10 (80.00 %)" in line
    assert "other 4/8" in line                                   # they no longer fall into the group "other"


def test_a_group_whose_rows_all_have_zero_calls_is_reported_not_divided(arm):
    rows = [row for row in SANE_ROWS if not row[1].startswith("bh_kurpark")] + [("PAID_METERED", "bh_kurpark", "shop", 0)]
    write_report(arm.report, rows)
    code, out, _ = arm.run()
    assert code == 0, out
    assert "bh no priced stay" in result_of(out, "no_feasible_option")


def test_a_hard_stop_group_whose_rows_all_have_zero_calls_fails_without_a_crash(arm):
    rows = [("PAID_METERED", "bs_zone_ia", "shop", 0)] + [row for row in SANE_ROWS if row[1] != "bs_zone_ia"]
    write_report(arm.report, rows)
    code, out, _ = arm.run()
    assert code == 1 and "bs_zone_ia has no priced stay" in result_of(out, "no_feasible_option")


def test_the_run_check_iteration_must_equal_the_iteration_of_the_report(arm, tmp_path):
    arm.report = write_report(tmp_path / "55.parking_outcomes.csv", SANE_ROWS)
    code, out, _ = arm.run()
    line = result_of(out, "module_active")
    assert code == 1 and line.startswith("[FAIL]") and "60" in line and "55" in line


def test_a_report_whose_file_name_has_no_iteration_fails_module_active(arm, tmp_path):
    arm.report = write_report(tmp_path / "report.csv", SANE_ROWS)
    code, out, _ = arm.run()
    assert code == 1 and "N.parking_outcomes.csv" in result_of(out, "module_active")


DRAW_LINE = ("INFO [parking] free-parking draw (RandomState(random_seed 0 + 4242), shift -0.100): 900/5000 persons (18.0 %) have a "
             "work/education activity in a street_paid or resident_zone zone, 450 of them (50.0 %) park free; 10/5000 persons "
             "(0.2 %) have one in a campus zone, 2 of them (20.0 %) park free; parkingFree on 460 of 910 eligible activities")


def test_the_person_level_free_draw_is_quoted_next_to_the_call_level_rate_from_whichever_log_has_it(arm, tmp_path):
    write_config(arm.config, parking_workplace_free_share_shift=-0.1)
    write_report(arm.report, FREE_ROWS)
    earlier = write_lines(tmp_path / "earlier_arm.log", [DRAW_LINE])
    arm.extra_logs = [earlier]
    code, out, _ = arm.run()
    line = result_of(out, "free_share")
    assert code == 0 and "employer_free 40/120" in line
    assert "450 of 900 persons" in line and "earlier_arm.log" in line and "NOTE" not in line


def test_a_logged_draw_shift_that_differs_from_the_config_is_noted_and_a_missing_draw_line_is_said(arm, tmp_path):
    write_report(arm.report, FREE_ROWS)                       # configured shift 0.0, the draw line says -0.100
    write_lines(arm.log, pipeline_lines() + [DRAW_LINE])
    code, out, _ = arm.run()
    assert code == 0 and "NOTE" in result_of(out, "free_share") and "-0.100" in result_of(out, "free_share")
    write_lines(arm.log, pipeline_lines())
    code, out, _ = arm.run()
    assert code == 0 and "no 'free-parking draw' line" in result_of(out, "free_share")


# ------------------------------------------------------------------------------- R-C-5: the arms without the zone model


def write_plain_config(path: Path, *, zones=False, urban=False, drop=None) -> Path:
    lines = ["config:", f"  parking_zones_enabled: {str(zones).lower()}", f"  enable_urban_parking: {str(urban).lower()}"]
    path.write_text("\n".join(line for line in lines if drop is None or drop not in line) + "\n", encoding="utf-8")
    return path


PLAIN_PIPELINE = ["2026-10-14 08:00:00 INFO [vrb-fares] prepared inputs (final schedule): facilities zoned 3/3",
                  "2026-10-14 09:30:00 INFO MATSim run finished"]
PLAIN_JAVA = ["INFO Controler [mode_choice] car cost model: legacy ring"]


class PlainArm:
    def __init__(self, tmp_path: Path, sanity, capsys, kind: str):
        self.kind, self.sanity, self.capsys, self.dir = kind, sanity, capsys, tmp_path
        self.config = write_plain_config(tmp_path / "arm_config.yml", urban=(kind == "legacy"))
        self.log = write_lines(tmp_path / "pipeline.log", PLAIN_PIPELINE)
        self.java_log = write_lines(tmp_path / "matsim.log", PLAIN_JAVA)

    def run(self, extra=()):
        code = self.sanity.main(["--arm-kind", self.kind, "--arm-config", str(self.config), "--log", str(self.log),
                                 "--java-log", str(self.java_log), *extra])
        captured = self.capsys.readouterr()
        return code, captured.out, captured.err


@pytest.fixture(params=["off", "legacy"])
def plain(request, tmp_path, sanity, capsys):
    return PlainArm(tmp_path, sanity, capsys, request.param)


def test_a_sane_arm_without_the_zone_model_needs_no_report_no_install_list_and_no_inputs_report(plain):
    code, out, err = plain.run()
    assert code == 0, out + err
    names = [line.split(" ", 2)[1].rstrip(":") for line in out.splitlines() if line.startswith("[")]
    assert names == ["arm_kind_config", "no_parking_log_lines"]
    assert out.splitlines()[0].startswith("[PASS] arm_kind_config:") and "ARM SANE" in out


def test_the_legacy_ring_says_that_it_rests_on_the_config_flag_only(sanity, tmp_path, capsys):
    legacy_dir, off_dir = tmp_path / "legacy", tmp_path / "off"
    legacy_dir.mkdir()
    off_dir.mkdir()
    code, out, _ = PlainArm(legacy_dir, sanity, capsys, "legacy").run()
    assert code == 0 and "config flag only" in result_of(out, "arm_kind_config")
    code, out, _ = PlainArm(off_dir, sanity, capsys, "off").run()
    assert code == 0 and "config flag only" not in result_of(out, "arm_kind_config")


@pytest.mark.parametrize("kind, zones, urban, word", [("off", True, False, "parking_zones_enabled is true"),
                                                      ("off", False, True, "enable_urban_parking is true"),
                                                      ("legacy", True, True, "parking_zones_enabled is true"),
                                                      ("legacy", False, False, "enable_urban_parking is false")])
def test_a_config_flag_that_contradicts_the_arm_kind_fails(sanity, tmp_path, capsys, kind, zones, urban, word):
    arm = PlainArm(tmp_path, sanity, capsys, kind)
    write_plain_config(arm.config, zones=zones, urban=urban)
    code, out, _ = arm.run()
    line = result_of(out, "arm_kind_config")
    assert code == 1 and line.startswith("[FAIL]") and word in line and out.splitlines()[-1].startswith("FIRST FAILURE")


@pytest.mark.parametrize("drop", ["parking_zones_enabled", "enable_urban_parking"])
def test_a_missing_flag_is_an_input_error_for_the_arms_without_the_zone_model(plain, drop):
    write_plain_config(plain.config, urban=(plain.kind == "legacy"), drop=drop)
    code, out, err = plain.run()
    assert code == 2 and drop in err and out == ""


@pytest.mark.parametrize("index, label", [(0, "[parking] run check"), (1, "[parking] prepared inputs"),
                                          (2, "[parking] zone release"), (3, "[parking] EV exemption:"),
                                          (4, "[parking] EV exemption is disabled")])
def test_a_zone_model_line_in_the_pipeline_log_of_an_arm_without_it_fails(plain, index, label):
    lines = {0: pipeline_lines()[-1], 1: pipeline_lines()[1], 2: pipeline_lines()[0], 3: pipeline_lines()[2],
             4: pipeline_lines(ev="off")[2]}
    write_lines(plain.log, PLAIN_PIPELINE + [lines[index]])
    code, out, _ = plain.run()
    line = result_of(out, "no_parking_log_lines")
    assert code == 1 and line.startswith("[FAIL]") and label in line and "pipeline.log" in line


@pytest.mark.parametrize("label, text", [("[parking] parking cost factor", java_lines()[2]),
                                         ("[parking] option price coefficient", java_lines()[3])])
def test_a_java_zone_model_line_in_the_matsim_log_of_an_arm_without_it_fails(plain, label, text):
    write_lines(plain.java_log, PLAIN_JAVA + [text])
    code, out, _ = plain.run()
    assert code == 1 and label in result_of(out, "no_parking_log_lines")


def test_without_a_java_log_the_java_lines_are_searched_in_the_pipeline_logs(plain, sanity, capsys):
    write_lines(plain.log, PLAIN_PIPELINE + [java_lines()[2]])
    code = sanity.main(["--arm-kind", plain.kind, "--arm-config", str(plain.config), "--log", str(plain.log)])
    assert code == 1 and "parking cost factor" in capsys.readouterr().out


def test_report_options_are_ignored_with_a_note_for_the_arms_without_the_zone_model(plain, tmp_path):
    code, out, err = plain.run(["--report", str(tmp_path / "x.csv"), "--expected-sources", str(tmp_path / "y.json")])
    assert code == 0 and "--report" in err and "ignored" in err


def write_simulation_output(root: Path, *, module=None, reports=(), gzipped=False, write_config_file=True) -> Path:
    (root / "ITERS" / "it.0").mkdir(parents=True)
    for report in reports:
        (root / "ITERS" / "it.0" / report).write_text(V4_HEADER + "\n", encoding="utf-8")
    if write_config_file:
        body = "<?xml version=\"1.0\" ?><config>"
        if module is not None:
            body += f'<module name="braunschweigParking"><param name="enabled" value="{module}" /></module>'
        body += "</config>"
        if gzipped:
            with gzip.open(root / "output_config.xml.gz", "wt", encoding="utf-8") as stream:
                stream.write(body)
        else:
            (root / "output_config.xml").write_text(body, encoding="utf-8")
    return root


@pytest.mark.parametrize("module", [None, "false"])
def test_the_output_of_an_arm_without_the_zone_model_has_no_report_and_no_enabled_module(plain, tmp_path, module):
    output = write_simulation_output(tmp_path / "simulation_output", module=module)
    code, out, err = plain.run(["--simulation-output", str(output)])
    assert code == 0, out + err
    assert result_of(out, "no_parking_outputs").startswith("[PASS]")


def test_the_gzipped_output_config_is_read_too(plain, tmp_path):
    output = write_simulation_output(tmp_path / "simulation_output", module="false", gzipped=True)
    assert plain.run(["--simulation-output", str(output)])[0] == 0
    output = write_simulation_output(tmp_path / "enabled", module="true", gzipped=True)
    code, out, _ = plain.run(["--simulation-output", str(output)])
    assert code == 1 and "enables the module" in result_of(out, "no_parking_outputs")


def test_an_enabled_module_in_the_output_config_fails(plain, tmp_path):
    output = write_simulation_output(tmp_path / "simulation_output", module="true")
    code, out, _ = plain.run(["--simulation-output", str(output)])
    assert code == 1 and "braunschweigParking" in result_of(out, "no_parking_outputs")


def test_an_outcome_report_in_the_output_of_an_arm_without_the_zone_model_fails(plain, tmp_path):
    output = write_simulation_output(tmp_path / "simulation_output", reports=["0.parking_outcomes.csv"])
    code, out, _ = plain.run(["--simulation-output", str(output)])
    assert code == 1 and "parking outcome report" in result_of(out, "no_parking_outputs")


def test_a_simulation_output_without_the_output_config_is_an_input_error(plain, tmp_path):
    output = write_simulation_output(tmp_path / "simulation_output", write_config_file=False)
    code, out, err = plain.run(["--simulation-output", str(output)])
    assert code == 2 and "output_config.xml" in err and out == ""
    code, out, err = plain.run(["--simulation-output", str(tmp_path / "absent")])
    assert code == 2 and "absent" in err


def test_the_output_config_file_name_is_the_one_matsim_names():
    """Drift guard: no Python code of the repository names the output config of a run, so the constant of this script is
    pinned to the MATSim 2026.0 library the Java side builds against (skipped where the Maven repository is absent)."""
    import zipfile

    jar = Path.home() / ".m2" / "repository" / "org" / "matsim" / "matsim" / "2026.0" / "matsim-2026.0.jar"
    if not jar.is_file():
        pytest.skip("matsim-2026.0.jar is not in the local Maven repository")
    with zipfile.ZipFile(jar) as archive:
        content = archive.read("org/matsim/core/config/groups/ControllerConfigGroup.class")
    assert b"output_config.xml" in content
    assert 'OUTPUT_CONFIG_FILE_NAME = "output_config.xml"' in SCRIPT.read_text(encoding="utf-8")
