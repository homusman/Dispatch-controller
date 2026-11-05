"""HAEO Dispatch Controller."""
from __future__ import annotations
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from .const import DOMAIN
from .coordinator import DispatchCoordinator

PLATFORMS = [Platform.SWITCH, Platform.NUMBER, Platform.SELECT, Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = DispatchCoordinator(hass, entry)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.start()
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = entry.runtime_data
    await coordinator.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
