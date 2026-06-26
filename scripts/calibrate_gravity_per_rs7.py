"""Thin shim. Logic moved to braunschweig.calibration._legacy_gravity_per_rs7
(behaviour-preserving migration into the calibration corner). Kept so existing
invocations and server/CI references keep working unchanged."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from braunschweig.calibration._legacy_gravity_per_rs7 import main  # noqa: E402

if __name__ == "__main__":
    main()
