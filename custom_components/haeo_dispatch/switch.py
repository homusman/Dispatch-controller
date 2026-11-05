"""Automatic dispatch enable/disable."""
from homeassistant.components.switch import SwitchEntity
from .entity import DispatchEntity

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EnableDispatch(entry.runtime_data)])

class EnableDispatch(DispatchEntity, SwitchEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "enabled", "Dispatch enabled")

    @property
    def is_on(self):
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs):
        self.coordinator.enabled = True
        await self.coordinator.tick()

    async def async_turn_off(self, **kwargs):
        self.coordinator.enabled = False
        self.coordinator.status = (
            "Dispatch disabled. Last inverter mode remains until manually changed."
        )
        self.coordinator.mode = "Disabled"
        self.coordinator.update()
