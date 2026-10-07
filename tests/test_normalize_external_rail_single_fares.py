import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.normalize_external_rail_single_fares import (
    DEFAULT_DEUTSCHLANDTARIF_PDF,
    DEFAULT_NIEDERSACHSENTARIF_PDF,
    ROOT,
    _local_source_path,
    _page_rows,
    _tiers,
    normalize,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "docs/data/external-regional-rail-single-fares-2026.json"

try:
    import pdfplumber  # noqa: F401  (optional source-extraction tooling)
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

# pdfplumber is not part of the pinned runtime environments (environments/*.lock); only a
# re-extraction from the official PDFs needs it. The committed table itself is pinned by
# test_committed_data_has_each_official_table_tier_and_source_evidence, which always runs.
needs_pdfplumber = pytest.mark.skipif(
    not HAS_PDFPLUMBER,
    reason="pdfplumber (optional source-extraction tooling) is not installed in this environment",
)


def test_pdf_text_rows_preserve_exact_euro_cents():
    rows = _page_rows("1 2,40 € 1,50 € 0,90 €\n2 3,00 € 1,80 € 1,05 €", 3)

    assert rows == {1: [240, 150, 90], 2: [300, 180, 105]}


def test_tiers_require_contiguous_published_distances_and_round_child_half_up():
    rows = {1: [201], 2: [200]}

    assert _tiers(rows, adult_column=0, child_column=None) == [
        {"up_to_km": 1, "adult_single_cents": 201, "child_single_cents": 101},
        {"up_to_km": 2, "adult_single_cents": 200, "child_single_cents": 100},
    ]
    with pytest.raises(ValueError, match="not contiguous"):
        _tiers({1: [100], 3: [300]}, adult_column=0, child_column=None)


def test_local_source_path_handles_relative_and_external_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)

    assert _local_source_path(Path("docs/data/external-regional-rail-single-fares-2026.json")) == (
        "docs/data/external-regional-rail-single-fares-2026.json"
    )
    assert _local_source_path(tmp_path / "outside.pdf") is None


def test_committed_data_has_each_official_table_tier_and_source_evidence():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    by_id = {item["tariff_id"]: item for item in data["tariffs"]}
    expected = {"niedersachsentarif": 500, "deutschlandtarif": 2000}

    for tariff_id, count in expected.items():
        tariff = by_id[tariff_id]
        tiers = tariff["tiers"]
        assert len(tiers) == count
        assert [tier["up_to_km"] for tier in tiers] == list(range(1, count + 1))
        source_hash = tariff["source"]["sha256"]
        assert re.fullmatch(r"[0-9a-f]{64}", source_hash)

    nds = by_id["niedersachsentarif"]["tiers"]
    assert nds[0] == {"up_to_km": 1, "adult_single_cents": 150, "child_single_cents": 75}
    assert nds[250] == {"up_to_km": 251, "adult_single_cents": 4760, "child_single_cents": 2380}
    assert nds[-1] == {"up_to_km": 500, "adult_single_cents": 7580, "child_single_cents": 3790}

    dt = by_id["deutschlandtarif"]["tiers"]
    assert dt[0] == {"up_to_km": 1, "adult_single_cents": 200, "child_single_cents": 100}
    assert dt[1000] == {"up_to_km": 1001, "adult_single_cents": 16060, "child_single_cents": 8030}
    assert dt[-1] == {"up_to_km": 2000, "adult_single_cents": 27770, "child_single_cents": 13885}
    assert all(item["effective_until"] is None for item in by_id.values())
    assert any("not physical route distances" in message for message in data["limitations"])
    assert by_id["niedersachsentarif"]["condition_reference"]["sections"] == "Teil III, 2.2.1-2.2.4"
    assert by_id["niedersachsentarif"]["condition_reference"]["url"].endswith(
        "NST_Befoerd_ab-01.09.2026_WEB.pdf"
    )
    assert by_id["deutschlandtarif"]["condition_reference"]["sections"].startswith("Teil A, sections 4.2.1-4.2.3")
    assert by_id["deutschlandtarif"]["condition_reference"]["url"].endswith(
        "20260528_A-1-TB-DT-Grundsaetze_Juni2026.pdf"
    )


@needs_pdfplumber
def test_source_backed_normalization_hashes_and_matches_local_official_pdfs():
    if not DEFAULT_NIEDERSACHSENTARIF_PDF.exists() or not DEFAULT_DEUTSCHLANDTARIF_PDF.exists():
        pytest.skip("official tariff PDFs are local-only eqasim-data inputs")

    committed = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    extracted = normalize(DEFAULT_NIEDERSACHSENTARIF_PDF, DEFAULT_DEUTSCHLANDTARIF_PDF)
    assert extracted == committed
    for tariff in committed["tariffs"]:
        source_path = ROOT / tariff["source"]["local_path"]
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        assert len(source_hash) == 64
        assert source_hash == tariff["source"]["sha256"]


@needs_pdfplumber
def test_cli_accepts_official_pdfs_outside_repository_with_stable_provenance(tmp_path):
    if not DEFAULT_NIEDERSACHSENTARIF_PDF.exists() or not DEFAULT_DEUTSCHLANDTARIF_PDF.exists():
        pytest.skip("official tariff PDFs are local-only eqasim-data inputs")

    external_niedersachsen = tmp_path / DEFAULT_NIEDERSACHSENTARIF_PDF.name
    external_deutschland = tmp_path / DEFAULT_DEUTSCHLANDTARIF_PDF.name
    shutil.copyfile(DEFAULT_NIEDERSACHSENTARIF_PDF, external_niedersachsen)
    shutil.copyfile(DEFAULT_DEUTSCHLANDTARIF_PDF, external_deutschland)
    output_path = tmp_path / "external-source-output.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/normalize_external_rail_single_fares.py"),
            "--niedersachsentarif-pdf",
            str(external_niedersachsen),
            "--deutschlandtarif-pdf",
            str(external_deutschland),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    output = json.loads(output_path.read_text(encoding="utf-8"))
    by_id = {tariff["tariff_id"]: tariff for tariff in output["tariffs"]}
    assert by_id["niedersachsentarif"]["source"]["local_path"] is None
    assert by_id["deutschlandtarif"]["source"]["local_path"] is None
    assert len(by_id["niedersachsentarif"]["source"]["sha256"]) == 64
    assert len(by_id["deutschlandtarif"]["source"]["sha256"]) == 64
    assert by_id["niedersachsentarif"]["tiers"] == json.loads(DATA_PATH.read_text(encoding="utf-8"))["tariffs"][0]["tiers"]
