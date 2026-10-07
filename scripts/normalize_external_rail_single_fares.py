"""Normalize official regional rail single-fare tables into a dated JSON input.

The official PDFs are local-only inputs under ``eqasim-data/external-tariffs``.
Extraction uses ``pdfplumber``, an optional source-extraction tool that is not part of
the pinned runtime environments (``environments/*.lock``); install it only to re-extract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NIEDERSACHSENTARIF_PDF = (
    ROOT / "eqasim-data/external-tariffs/niedersachsentarif-2025-12.pdf"
)
DEFAULT_DEUTSCHLANDTARIF_PDF = (
    ROOT / "eqasim-data/external-tariffs/deutschlandtarif-2025-12.pdf"
)
DEFAULT_OUTPUT = ROOT / "docs/data/external-regional-rail-single-fares-2026.json"

NIEDERSACHSENTARIF_URL = (
    "https://www.niedersachsentarif.de/wp-content/uploads/2025/12/"
    "Preismatrix_Niedersachsentarif_gueltig_ab_14.12.2025.pdf"
)
DEUTSCHLANDTARIF_URL = (
    "https://www.deutschlandtarifverbund.de/wp-content/uploads/2025/12/"
    "20251118_J_Preisliste_D-Tarif_Dez.2025.pdf"
)

# These pins make the committed values reproducible against reviewed official
# PDFs. Replacing a source file requires an intentional update to this script.
SOURCE_SHA256 = {
    "niedersachsentarif": "9e0aa1b359def0589ed02f4b5d2d2b8e390f17370daa4fdcf4c1fde110d5d035",
    "deutschlandtarif": "e5c7cdadc00969910d46b16af279afd7af9db21f39815b3c2820864d902c74cc",
}

ROW_RE = re.compile(r"^\s*(\d{1,4})\s+(.+?)\s*$")
AMOUNT_RE = re.compile(r"(\d+)[,.](\d{2})")


def _amount_to_cents(value: str) -> int:
    """Parse an official decimal euro value without binary floating point."""
    normalized = value.replace(",", ".")
    return int(Decimal(normalized) * 100)


def _page_rows(text: str, expected_amounts: int) -> dict[int, list[int]]:
    rows: dict[int, list[int]] = {}
    for line in text.splitlines():
        match = ROW_RE.match(line)
        if not match:
            continue
        distance_km = int(match.group(1))
        amounts = [_amount_to_cents(item.group(0)) for item in AMOUNT_RE.finditer(match.group(2))]
        if len(amounts) != expected_amounts:
            continue
        if distance_km in rows:
            raise ValueError(f"Duplicate tariff distance row: {distance_km} km")
        rows[distance_km] = amounts
    return rows


def _collect_rows(reader, page_indexes: Iterable[int], expected_amounts: int) -> dict[int, list[int]]:
    rows: dict[int, list[int]] = {}
    for page_index in page_indexes:
        for distance_km, amounts in _page_rows(reader.pages[page_index].extract_text(), expected_amounts).items():
            if distance_km in rows:
                raise ValueError(f"Duplicate tariff distance row: {distance_km} km")
            rows[distance_km] = amounts
    return rows


def _tiers(rows: dict[int, list[int]], adult_column: int, child_column: int | None) -> list[dict[str, int]]:
    if not rows:
        raise ValueError("No tariff rows were extracted")
    expected_distances = list(range(1, max(rows) + 1))
    if sorted(rows) != expected_distances:
        missing = sorted(set(expected_distances) - rows.keys())
        raise ValueError(f"Tariff distance rows are not contiguous; missing: {missing[:10]}")
    result = []
    for distance_km in expected_distances:
        adult_cents = rows[distance_km][adult_column]
        # Deutschlandtarif rules apply 50% to the normal fare, rounded half-up
        # to a whole cent. Integer arithmetic implements that exactly.
        child_cents = rows[distance_km][child_column] if child_column is not None else (adult_cents + 1) // 2
        result.append(
            {
                "up_to_km": distance_km,
                "adult_single_cents": adult_cents,
                "child_single_cents": child_cents,
            }
        )
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _local_source_path(path: Path) -> str | None:
    """Return a stable repository-relative path, or null for external inputs."""
    resolved_path = path.resolve()
    try:
        return resolved_path.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return None


def _source(path: Path, authority: str) -> dict[str, str | None]:
    actual_hash = _sha256(path)
    expected_hash = SOURCE_SHA256[authority]
    if actual_hash != expected_hash:
        raise ValueError(
            f"{authority} source SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
        )
    return {
        "url": NIEDERSACHSENTARIF_URL if authority == "niedersachsentarif" else DEUTSCHLANDTARIF_URL,
        "local_path": _local_source_path(path),
        "sha256": actual_hash,
    }


def normalize(niedersachsen_pdf: Path, deutschland_pdf: Path) -> dict:
    try:
        import pdfplumber
    except ImportError as error:
        raise RuntimeError("Install pdfplumber to extract tariff rows from the source PDFs") from error

    with pdfplumber.open(niedersachsen_pdf) as niedersachsen_reader:
        # Printed pages 1-8 are adult single tickets; 9-16 are child singles.
        adult_rows = _collect_rows(niedersachsen_reader, range(0, 8), 9)
        child_rows = _collect_rows(niedersachsen_reader, range(8, 16), 9)
    if sorted(adult_rows) != list(range(1, 501)) or sorted(child_rows) != list(range(1, 501)):
        raise ValueError("Niedersachsentarif single-ticket tables must cover tariff distances 1-500 km")
    n_rows: dict[int, list[int]] = {}
    for distance_km, adult_values in adult_rows.items():
        if distance_km not in child_rows:
            raise ValueError(f"Niedersachsentarif child fare missing at {distance_km} km")
        n_rows[distance_km] = [adult_values[1], child_rows[distance_km][1]]

    with pdfplumber.open(deutschland_pdf) as deutschland_reader:
        # Printed pages 3-90 (PDF pages 4-91) are the complete 1-2,000 km
        # Normalpreis table for Nahverkehrszüge.
        d_rows = _collect_rows(deutschland_reader, range(3, 91), 4)
    if sorted(d_rows) != list(range(1, 2001)):
        raise ValueError("Deutschlandtarif Normalpreis table must cover tariff distances 1-2000 km")

    return {
        "schema_version": "1.0",
        "currency": "EUR",
        "price_unit": "cent",
        "fare_product": "single",
        "class": "second",
        "distance_basis": "published_tariff_distance_km",
        "limitations": [
            "up_to_km values are tariff-distance breakpoints published in each official table.",
            "They are not physical route distances and must not be filled from route length.",
            "No origin-destination tariff-distance lookup is included; mapping a journey to a breakpoint remains a separate assumption unless exact tariff distance is supplied.",
            "No fare is provided above the source table extent.",
        ],
        "tariffs": [
            {
                "tariff_id": "niedersachsentarif",
                "authority": "Niedersachsentarif",
                "applicability": "Relations governed by the Niedersachsentarif; ordinary adult or child single journey, second class.",
                "valid_from": "2025-12-14",
                "effective_until": None,
                "table_reference": {
                    "title": "Preisliste Niedersachsentarif ab 14.12.2025, section 1, Einzelfahrkarte Erwachsener and Einzelfahrkarte Kind",
                    "adult_table_pages": "printed pages 1-8 (PDF pages 1-8)",
                    "child_table_pages": "printed pages 9-16 (PDF pages 9-16)",
                    "distance_column": "Tarifentfernung",
                    "price_column": "Einfache Fahrt ... Normalpreis, 2. Wagenklasse",
                },
                "child_applicability": "Ages 6 through 14 traveling without the free accompanied-child entitlement; children up to age 5 travel free with a supervisor, and up to three children ages 6-14 travel free with an eligible adult ticket.",
                "child_price_method": "directly transcribed from the official Einzelfahrkarte Kind table",
                "condition_reference": {
                    "url": "https://www.niedersachsentarif.de/wp-content/uploads/2026/08/NST_Befoerd_ab-01.09.2026_WEB.pdf",
                    "sections": "Teil III, 2.2.1-2.2.4",
                    "valid_from": "2026-09-01",
                },
                "source": _source(niedersachsen_pdf, "niedersachsentarif"),
                "tiers": _tiers(n_rows, adult_column=0, child_column=1),
            },
            {
                "tariff_id": "deutschlandtarif",
                "authority": "Deutschlandtarifverbund-GmbH",
                "applicability": "Deutschlandtarif normal price for eligible Nahverkehrszüge, ordinary single journey, second class.",
                "valid_from": "2025-12-14",
                "effective_until": None,
                "table_reference": {
                    "title": "Preisliste des Deutschlandtarifs, section 3, Normalpreis für Nahverkehrszüge des Deutschlandtarifs; Tarifstand Bundesweit 14.12.2025",
                    "pages": "printed pages 3-90 (PDF pages 4-91)",
                    "distance_column": "bis ... km",
                    "price_column": "Einfache Fahrt, 2. Klasse",
                },
                "child_applicability": "Ages 6 through 14 traveling alone; up to age 5 are free with a supervisor, and up to three children ages 6-14 travel free with an adult holding a normal-price ticket.",
                "child_price_method": "50 percent of normal price, rounded half-up to a whole cent under the price-list rounding rules; computed from the published adult fare",
                "condition_reference": {
                    "url": "https://www.deutschlandtarifverbund.de/wp-content/uploads/2026/06/20260528_A-1-TB-DT-Grundsaetze_Juni2026.pdf",
                    "sections": "Teil A, sections 4.2.1-4.2.3; Teil J price-list section 2 rounding rules",
                    "stand": "2026-06-14",
                },
                "source": _source(deutschland_pdf, "deutschlandtarif"),
                "tiers": _tiers(d_rows, adult_column=0, child_column=None),
            },
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--niedersachsentarif-pdf", type=Path, default=DEFAULT_NIEDERSACHSENTARIF_PDF)
    parser.add_argument("--deutschlandtarif-pdf", type=Path, default=DEFAULT_DEUTSCHLANDTARIF_PDF)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    data = normalize(args.niedersachsentarif_pdf, args.deutschlandtarif_pdf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
