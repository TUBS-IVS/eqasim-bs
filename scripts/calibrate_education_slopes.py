"""Thin shim. Logic moved to braunschweig.calibration._legacy_education_slopes
(behaviour-preserving migration into the calibration corner). Kept so existing
invocations and server/CI references keep working unchanged."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from braunschweig.calibration._legacy_education_slopes import (  # noqa: E402
    main,
    mean_distance_for_slope,
    mean_distance_decay,
    secant_calibrate_slope,
    calibrate_level_per_rs7,
)

if __name__ == "__main__":
    main()
