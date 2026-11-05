"""Visible plan, status and controller targets."""
from homeassistant.components.sensor import SensorEntity
from .entity import DispatchEntity

SENSORS = (
    ("mode", "Dispatch mode", None),
    ("status", "Dispatch status", None),
    ("grid_target_w", "HAEO grid target", "W"),
    ("grid_actual_w", "Measured grid power", "W"),
    ("grid_error_w", "Grid tracking error", "W"),
    ("leaf_plan_w", "HAEO Leaf power", "W"),
    ("tesla_plan_w", "HAEO Tesla power", "W"),
    ("leaf_command_w", "Solis command", "W"),
    ("tesla_command_w", "Fronius command", "W"),
    ("tesla_write_status", "Fronius write status", None),
    ("tesla_write_time", "Fronius last write time", None),
    ("tesla_effective_target_w", "Fronius limited target", "W"),
    ("tesla_limit_reason", "Fronius limiting reason", None),
    ("tesla_actuator_w", "Fronius actuator value", "W"),
    ("tesla_switch_state", "Fronius Self-Use switch state", None),
)
async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([DispatchSensor(entry.runtime_data, *args) for args in SENSORS])

class DispatchSensor(DispatchEntity, SensorEntity):
    def __init__(self, coordinator, key, name, unit):
        super().__init__(coordinator, key, name)
        if unit is not None:
            self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self):
        return getattr(self.coordinator, self._key)
