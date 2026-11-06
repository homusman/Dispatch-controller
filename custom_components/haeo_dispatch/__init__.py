"""HAEO Dispatch Controller."""
from __future__ import annotations
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from .const import DOMAIN
from .coordinator import DispatchCoordinator

PLATFORMS = [Platform.SWITCH, Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = DispatchCoordinator(hass, entry)
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.start()
    return True

async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = entry.runtime_data
    await coordinator.stop()
    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # ConfigEntry owns callbacks registered through async_on_unload;
    # Home Assistant clears them on unload. No manual cleanup API exists.
    return result
