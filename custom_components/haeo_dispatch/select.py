"""Priority for distributing bounded grid-error corrections."""
from homeassistant.components.select import SelectEntity
from .entity import DispatchEntity

OPTIONS = ["Leaf first", "Tesla first", "SoC headroom"]
async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([BatteryPriority(entry.runtime_data)])

class BatteryPriority(DispatchEntity, SelectEntity):
    _attr_options = OPTIONS

    def __init__(self, coordinator):
        super().__init__(coordinator, "priority", "Battery correction priority")

    @property
    def current_option(self):
        return self.coordinator.priority

    async def async_select_option(self, option):
        if option not in OPTIONS:
            raise ValueError(f"Unsupported priority {option}")
        self.coordinator.priority = option
        self.coordinator.update()
