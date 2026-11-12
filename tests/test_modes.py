"""Exercise mode boundaries without Home Assistant or inverter commands."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "dispatch_mode", Path(__file__).parents[1] / "custom_components/haeo_dispatch/mode.py"
)
mode = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mode)


class ModeTests(unittest.TestCase):
    def test_import_and_export_follow_the_same_hysteresis(self):
        for sign in (1, -1):
            previous = None
            for power, expected in (
                (100, "Self-use"), (150, "Self-use"), (200, "Self-use"),
                (200.01, "Arbitrage"), (150, "Arbitrage"), (100.01, "Arbitrage"),
                (100, "Self-use"), (150, "Self-use"),
            ):
                previous = mode.select_mode(sign * power, 0, -3500, previous, 100, 100)
                self.assertEqual(previous, expected, (sign, power))

    def test_zero_hysteresis_matches_the_original_grid_threshold(self):
        for power, expected in ((100, "Self-use"), (100.01, "Arbitrage")):
            self.assertEqual(mode.select_mode(power, 0, -3500, None, 100, 0), expected)

    def test_hold_has_priority_and_clears_the_arbitrage_latch(self):
        previous = mode.select_mode(300, 80, -80, "Arbitrage", 100, 100)
        self.assertEqual(previous, "Hold")
        self.assertEqual(mode.select_mode(150, 0, -3500, previous, 100, 100), "Self-use")

    def test_thresholds_can_be_zero(self):
        self.assertEqual(mode.select_mode(0, 0, 1000, None, 0, 0), "Self-use")
        self.assertEqual(mode.select_mode(0.01, 0, 1000, None, 0, 0), "Arbitrage")

    def test_invalid_thresholds_are_rejected(self):
        for value in (-1, float("nan"), float("inf"), -float("inf"), "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mode.nonnegative_watts(value)
        self.assertEqual(mode.nonnegative_watts("125.5"), 125.5)
