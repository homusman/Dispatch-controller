"""Tunable bounds for the slow correction loop."""
from homeassistant.components.number import NumberEntity, NumberMode
from .entity import DispatchEntity

PARAMETERS = (
    ("max_adjust_w", "Max feedback adjustment", 0, 3000, 50, "W"),
    ("gain", "Feedback gain", 0, 1, 0.05, None),
    ("max_step_w", "Max command step per tick", 100, 3000, 50, "W"),
)
async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([DispatchNumber(entry.runtime_data, *args) for args in PARAMETERS])

class DispatchNumber(DispatchEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    def __init__(self, coordinator, key, name, low, high, step, unit):
        super().__init__(coordinator, key, name)
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = step
        if unit is not None:
            self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self):
        return getattr(self.coordinator, self._key)

    async def async_set_native_value(self, value):
        setattr(self.coordinator, self._key, float(value))
        self.coordinator.update()
