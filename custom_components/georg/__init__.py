"""GeORG heating control: switch heaters according to GeORG room bookings."""

from __future__ import annotations

from homeassistant.const import CONF_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GeorgClient
from .coordinator import GeorgConfigEntry, GeorgCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: GeorgConfigEntry) -> bool:
    """Set up GeORG from a config entry."""
    client = GeorgClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN]
    )
    coordinator = GeorgCoordinator(hass, entry, client)
    await coordinator.async_load()
    # Not async_config_entry_first_refresh: the integration must keep running
    # (and its failsafe active) even if GeORG is unreachable at startup.
    await coordinator.async_refresh()
    entry.runtime_data = coordinator

    # Changed options reload the entry (OptionsFlowWithReload); the restart
    # then reports with ``force: true``.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GeorgConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GeorgConfigEntry) -> None:
    """Remove persisted state when the integration is deleted."""
    client = GeorgClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN]
    )
    await GeorgCoordinator(hass, entry, client).async_remove_store()
