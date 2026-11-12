"""Check normal dispatcher ticks and settings with simulated HA entities."""

from datetime import datetime, timezone
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

from homeassistant.helpers import config_validation as cv
import voluptuous_serialize

# Import the controller without setting up an actual Home Assistant integration.
package = ModuleType("dispatch_under_test")
package.__path__ = [str(Path(__file__).parents[1] / "custom_components/haeo_dispatch")]
sys.modules.setdefault(package.__name__, package)
DispatchCoordinator = importlib.import_module("dispatch_under_test.coordinator").DispatchCoordinator
flow = importlib.import_module("dispatch_under_test.config_flow")


def state(value, **attributes):
    return SimpleNamespace(state=str(value), attributes=attributes, last_reported=datetime.now(timezone.utc))


class States:
    def __init__(self):
        self.values = {}

    def get(self, entity_id):
        return self.values.get(entity_id)

    def is_state(self, entity_id, value):
        current = self.get(entity_id)
        return current is not None and current.state == value


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.states = States()
        self.calls = []
        config = {
            "grid_positive_import": True, "leaf_positive_discharge": True,
            "tesla_positive_discharge": True, "fallback_mode": "hold",
        }
        for field in ("grid_plan", "leaf_plan", "tesla_plan", "grid_actual", "leaf_actual", "tesla_actual"):
            entity_id = "sensor." + field
            config[field] = entity_id
            self.states.values[entity_id] = state(0, unit_of_measurement="kW")
        for which in ("leaf", "tesla"):
            config[which + "_number"] = "number." + which
            config[which + "_switch"] = "switch." + which
            self.states.values["number." + which] = state(0, min=-5000, max=5000)
            self.states.values["switch." + which] = state("off")
        self.states.values["sensor.tesla_plan"] = state(-3.5, unit_of_measurement="kW")
        self.entry = SimpleNamespace(data=config, options={})
        self.hass = SimpleNamespace(
            states=self.states, services=SimpleNamespace(async_call=self.service_call)
        )
        self.controller = DispatchCoordinator(self.hass, self.entry)

    async def service_call(self, domain, service, data, blocking):
        self.calls.append((domain, service, dict(data)))
        entity_id = data["entity_id"]
        self.states.values[entity_id].state = (
            str(data["value"]) if domain == "number" else "on" if service == "turn_on" else "off"
        )

    def grid(self, watts):
        self.states.values["sensor.grid_plan"] = state(watts / 1000, unit_of_measurement="kW")

    async def test_shadow_ticks_remember_selection_without_actuator_writes(self):
        for watts, expected in ((150, "Self-use"), (201, "Arbitrage"), (150, "Arbitrage"), (100, "Self-use")):
            self.grid(watts)
            await self.controller.tick()
            self.assertEqual(self.controller.mode, "Disabled")
            self.assertIn("Shadow | " + expected, self.controller.status)
        self.assertEqual(self.calls, [])

    async def test_live_ticks_use_same_thresholds_and_keep_battery_setpoints(self):
        self.controller.enabled = True
        self.grid(150)
        await self.controller.tick()
        self.assertEqual(self.controller.mode, "Self-use")
        self.assertTrue(self.states.is_state("switch.leaf", "on"))
        self.assertEqual(self.controller.tesla_command_w, -3500)
        self.grid(201)
        await self.controller.tick()
        self.assertEqual(self.controller.mode, "Arbitrage")
        self.assertTrue(self.states.is_state("switch.leaf", "off"))
        self.grid(150)
        await self.controller.tick()
        self.assertEqual(self.controller.mode, "Arbitrage")
        self.grid(100)
        await self.controller.tick()
        self.assertEqual(self.controller.mode, "Self-use")
        self.assertTrue(self.states.is_state("switch.leaf", "on"))

    async def test_invalid_plan_resets_hysteresis_in_shadow_and_live_modes(self):
        for enabled in (False, True):
            self.controller.enabled = enabled
            self.grid(201)
            await self.controller.tick()
            self.states.values["sensor.grid_plan"].state = "unavailable"
            await self.controller.tick()
            self.assertEqual(self.controller.mode, "Error")
            self.assertIsNone(self.controller._selected_mode)
            self.grid(150)
            await self.controller.tick()
            self.assertEqual(self.controller._selected_mode, "Self-use")

    async def test_explicit_stop_resets_hysteresis(self):
        self.grid(201)
        await self.controller.tick()
        await self.controller._safe_stop("User disabled dispatch")
        self.assertIsNone(self.controller._selected_mode)

    async def test_actuator_fault_resets_hysteresis_before_recovery(self):
        self.controller.enabled = True
        self.grid(201)
        await self.controller.tick()
        self.states.values["number.tesla"].attributes.pop("min")
        await self.controller.tick()
        self.assertEqual(self.controller.mode, "Error")
        self.assertIsNone(self.controller._selected_mode)
        self.states.values["number.tesla"].attributes["min"] = -5000
        self.grid(150)
        await self.controller.tick()
        self.assertEqual(self.controller.mode, "Self-use")

    async def test_options_override_saved_data_and_survive_recreation(self):
        self.entry.data["grid_deadband_w"] = 10
        self.entry.options = {"grid_deadband_w": 250, "grid_hysteresis_w": 50}
        for _ in range(2):
            controller = DispatchCoordinator(self.hass, self.entry)
            self.assertFalse(controller.enabled)
            self.grid(300)
            await controller.tick()
            self.assertEqual(controller._selected_mode, "Self-use")
            self.grid(301)
            await controller.tick()
            self.assertEqual(controller._selected_mode, "Arbitrage")
            self.grid(250)
            await controller.tick()
            self.assertEqual(controller._selected_mode, "Self-use")


class SettingsTests(unittest.TestCase):
    def valid_input(self):
        return {"name": "Test", **{key: domain + "." + key for key, _, domain in flow.FIELDS}}

    def test_existing_entries_get_default_thresholds(self):
        result = flow.schema()(self.valid_input())
        self.assertEqual(result["grid_deadband_w"], 100)
        self.assertEqual(result["grid_hysteresis_w"], 100)

    def test_settings_validate_fractional_and_invalid_thresholds(self):
        for key in ("grid_deadband_w", "grid_hysteresis_w"):
            for value in (-1, float("nan"), float("inf"), "invalid"):
                with self.subTest(key=key, value=value):
                    self.assertEqual(
                        flow.validate_grid_settings({**self.valid_input(), key: value}),
                        {key: "invalid_grid_threshold"},
                    )
            result = flow.schema()({**self.valid_input(), key: "25.5"})
            self.assertEqual(result[key], 25.5)

    def test_threshold_fields_serialize_as_numeric_settings(self):
        fields = voluptuous_serialize.convert(flow.schema(), custom_serializer=cv.custom_serializer)
        numeric = [field for field in fields if field["name"] in ("grid_deadband_w", "grid_hysteresis_w")]
        self.assertEqual(len(numeric), 2)
        self.assertTrue(all(field["selector"]["number"]["unit_of_measurement"] == "W" for field in numeric))
