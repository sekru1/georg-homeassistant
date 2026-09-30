"""Binary sensors for GeORG."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GeorgConfigEntry, GeorgCoordinator, RoomConfig
from .entity import GeorgEntity, GeorgRoomEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GeorgConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [GeorgConnectionSensor(coordinator)]
    entities.extend(
        GeorgHeatingSensor(coordinator, room)
        for room in coordinator.room_configs.values()
    )
    async_add_entities(entities)


class GeorgHeatingSensor(GeorgRoomEntity, BinarySensorEntity):
    """Last received request: heat or not."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, coordinator: GeorgCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "heating")

    @property
    def is_on(self) -> bool | None:
        command = self.room_state.command
        return command.is_on if command else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.room_state
        return {
            "received_at": state.received_at,
            "failsafe": state.failsafe,
            "pending_devices": state.pending,
        }


class GeorgConnectionSensor(GeorgEntity, BinarySensorEntity):
    """Whether the last report to GeORG succeeded."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: GeorgCoordinator) -> None:
        super().__init__(coordinator, "connection")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success
