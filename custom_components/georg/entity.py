"""Base entities for GeORG."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GeorgCoordinator, RoomConfig, RoomState


class GeorgEntity(CoordinatorEntity[GeorgCoordinator]):
    """Entity belonging to the GeORG connection device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GeorgCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="GeORG-Verbindung",
            manufacturer="GeORG",
            model="Heizungssteuerung",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=coordinator.client.base_url,
        )


class GeorgRoomEntity(CoordinatorEntity[GeorgCoordinator]):
    """Entity belonging to a mapped GeORG room."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: GeorgCoordinator, room: RoomConfig, key: str
    ) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self.room_id = room.id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{room.id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{room.id}")},
            name=room.name,
            manufacturer="GeORG",
            model="Raum",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        # Room entities show the last known (persisted) state, also while
        # GeORG is unreachable.
        return True

    @property
    def room_state(self) -> RoomState:
        return self.coordinator.rooms.get(self.room_id) or RoomState()
