"""Config flow for HAEO Dispatch Controller."""
from __future__ import annotations
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import DOMAIN

FIELDS = [
    ("grid_plan", "HAEO grid active power (+import, kW)", "sensor"),
    ("leaf_plan", "HAEO Leaf active power (+discharge, kW)", "sensor"),
    ("tesla_plan", "HAEO Tesla active power (+discharge, kW)", "sensor"),
    ("grid_actual", "Actual grid power (W or kW)", "sensor"),
    ("leaf_actual", "Solis measured battery power (W or kW)", "sensor"),
    ("tesla_actual", "Fronius measured battery power (W or kW)", "sensor"),
    ("leaf_soc", "Leaf SoC (%)", "sensor"),
    ("tesla_soc", "Tesla SoC (%)", "sensor"),
    ("leaf_number", "Solis forced battery power", "number"),
    ("tesla_number", "Fronius forced battery power", "number"),
    ("leaf_switch", "Solis Self-Use Mode", "switch"),
    ("tesla_switch", "Fronius Self-Use Mode", "switch"),
]
def schema(values=None):
    values = values or {}
    fields = {
        vol.Required("name", default=values.get("name", "HAEO Dispatch")): str,
        vol.Required("grid_positive_import", default=values.get("grid_positive_import", True)): bool,
        vol.Required("leaf_positive_discharge", default=values.get("leaf_positive_discharge", True)): bool,
        vol.Required("tesla_positive_discharge", default=values.get("tesla_positive_discharge", True)): bool,
    }
    for key, _label, domain in FIELDS:
        fields[vol.Required(key, default=values[key] if key in values else vol.UNDEFINED)] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain=domain)
        )
    return vol.Schema(fields)

class HAEOConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id("single_controller")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input["name"], data=user_input
            )
        return self.async_show_form(step_id="user", data_schema=schema())

    @staticmethod
    def async_get_options_flow(config_entry):
        return HAEOOptionsFlow()


class HAEOOptionsFlow(config_entries.OptionsFlow):
    """Allow editing sensors, signs and actuators without removing hub."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=schema(current),
        )
