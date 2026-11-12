"""Count the MiD person item-nonresponse codes that popsim imputes (ADR-0192 exposure).

Reads the raw MiD 2023 B1 persons file (comma-separated, latin-1, as the other MiD
extractors in ``scripts/``) and counts, over ALL persons and unfiltered:

* ``P_BKAT`` nonresponse codes of ``map_employment_status`` (generic nonresponse codes
  that are not in ``EMPLOYMENT_STATUS_BY_P_BKAT``; in practice code 9),
* ``P_FKARTE`` nonresponse codes of ``map_pt_subscription_type`` (generic nonresponse
  codes not in ``FKARTE_TO_CATEGORY`` plus ``PT_SUBSCRIPTION_IMPUTE_CODES``; 99, 202, 206).

The code sets are imported from ``braunschweig.popsim.attributes`` / ``.missing``, not
retyped. Missing cells (NaN) are reported separately. Output: counts and shares on stdout.

Usage: python scripts/count_mid_person_nonresponse.py --persons-csv <MiD2023_Personen.csv>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from braunschweig.popsim import attributes, missing  # noqa: E402

CSV_READ_KWARGS = dict(sep=",", encoding="latin-1", low_memory=False)


def nonresponse_codes(value_map, impute_codes=()) -> frozenset:
    """Codes ``missing.resolve`` treats as nonresponse for a spec (same rule as there)."""
    return (missing.NONRESPONSE_CODES - set(value_map)) | set(impute_codes)


def count_codes(series: pd.Series, codes) -> dict:
    """Per-code counts (all codes in ``codes``, zero included) plus NaN count and total."""
    numeric = pd.to_numeric(series, errors="coerce")
    counts = {int(c): int((numeric == c).sum()) for c in sorted(codes)}
    return {"by_code": counts, "n_nan": int(numeric.isna().sum()), "n_persons": len(series)}


def share(count: int, n: int) -> str:
    """``count`` as a percentage of ``n``; an empty persons file has no share."""
    return f"{100.0 * count / n:.2f} %" if n else "n/a"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--persons-csv", required=True, type=Path,
                        help="Raw MiD 2023 B1 MiD2023_Personen.csv")
    args = parser.parse_args(argv)
    if not args.persons_csv.is_file():
        parser.error(f"persons file not found: {args.persons_csv}")
    persons = pd.read_csv(args.persons_csv, usecols=["P_BKAT", "P_FKARTE"], **CSV_READ_KWARGS)
    if persons.empty:
        print(f"warning: {args.persons_csv} holds no persons; every share is n/a", file=sys.stderr)
    specs = {
        "P_BKAT": nonresponse_codes(attributes.EMPLOYMENT_STATUS_BY_P_BKAT),
        "P_FKARTE": nonresponse_codes(attributes.FKARTE_TO_CATEGORY,
                                      attributes.PT_SUBSCRIPTION_IMPUTE_CODES),
    }
    for col, codes in specs.items():
        res = count_codes(persons[col], codes)
        n = res["n_persons"]
        print(f"{col}: persons {n}")
        total = 0
        for code, k in res["by_code"].items():
            total += k
            print(f"  code {code}: {k} ({share(k, n)})")
        print(f"  nonresponse codes total: {total} ({share(total, n)}); "
              f"NaN cells: {res['n_nan']} ({share(res['n_nan'], n)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
