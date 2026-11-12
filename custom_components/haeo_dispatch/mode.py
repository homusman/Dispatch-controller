"""Select dispatch modes using planned grid power and a hysteresis band."""

import math

DEFAULT_GRID_DEADBAND_W = 100.0
DEFAULT_GRID_HYSTERESIS_W = 100.0
BATTERY_TOLERANCE_W = 80.0


def nonnegative_watts(value):
    """Validate a finite nonnegative power threshold in watts."""
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Enter a finite nonnegative number of watts")
    return number


def select_mode(grid, leaf, tesla, previous, deadband, hysteresis):
    """Use a wider grid threshold to enter Arbitrage than to leave it."""
    if abs(leaf) <= BATTERY_TOLERANCE_W and abs(tesla) <= BATTERY_TOLERANCE_W:
        return "Hold"
    threshold = deadband if previous == "Arbitrage" else deadband + hysteresis
    return "Self-use" if abs(grid) <= threshold else "Arbitrage"
