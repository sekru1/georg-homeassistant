"""Switch to enable or disable GeORG control per room."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GeorgConfigEntry, GeorgCoordinator, RoomConfig
from .entity import GeorgRoomEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GeorgConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        GeorgAutomationSwitch(coordinator, room)
        for room in coordinator.room_configs.values()
    )


class GeorgAutomationSwitch(GeorgRoomEntity, SwitchEntity):
    """Off = requests from GeORG are ignored for this room (HA only)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GeorgCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "automation")

    @property
    def is_on(self) -> bool:
        return self.room_state.automation

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_automation(self.room_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_automation(self.room_id, False)
