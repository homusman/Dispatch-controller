"""HAEO Dispatch entities."""
from homeassistant.helpers.entity import Entity
class DispatchEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    def __init__(self, coordinator, key, name):
        self.coordinator = coordinator
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.unique_prefix}_{key}"
        coordinator.add_listener(self)

    @property
    def device_info(self):
        return self.coordinator.device_info
