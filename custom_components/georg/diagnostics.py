"""Diagnostics for GeORG."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import GeorgConfigEntry

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GeorgConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "connection": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception)
            if coordinator.last_exception
            else None,
            "last_success": coordinator.last_success,
        },
        "excluded_rooms": coordinator.excluded_rooms,
        "room_info": [room.as_dict() for room in coordinator.room_info.values()],
        "rooms": {
            room_id: state.as_dict() for room_id, state in coordinator.rooms.items()
        },
    }
