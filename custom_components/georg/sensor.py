"""Sensors for GeORG."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GeorgConfigEntry, GeorgCoordinator, RoomConfig, RoomState
from .entity import GeorgEntity, GeorgRoomEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GeorgRoomSensorDescription(SensorEntityDescription):
    value_fn: Callable[[RoomState], float | str | datetime | None]


ROOM_SENSORS = (
    GeorgRoomSensorDescription(
        key="target_temperature",
        translation_key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda s: s.command.target_temperature if s.command else None,
    ),
    GeorgRoomSensorDescription(
        key="window_start",
        translation_key="window_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.window.starts_at if s.window else None,
    ),
    GeorgRoomSensorDescription(
        key="window_end",
        translation_key="window_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.window.ends_at if s.window else None,
    ),
    GeorgRoomSensorDescription(
        key="window_name",
        translation_key="window_name",
        value_fn=lambda s: s.window.name if s.window else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GeorgConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [GeorgLastSuccessSensor(coordinator)]
    entities.extend(
        GeorgRoomSensor(coordinator, room, description)
        for room in coordinator.room_configs.values()
        for description in ROOM_SENSORS
    )
    async_add_entities(entities)


class GeorgRoomSensor(GeorgRoomEntity, SensorEntity):
    """Information about the last request and heating window of a room."""

    entity_description: GeorgRoomSensorDescription

    def __init__(
        self,
        coordinator: GeorgCoordinator,
        room: RoomConfig,
        description: GeorgRoomSensorDescription,
    ) -> None:
        super().__init__(coordinator, room, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | datetime | None:
        return self.entity_description.value_fn(self.room_state)


class GeorgLastSuccessSensor(GeorgEntity, SensorEntity):
    """Time of the last successful report to GeORG."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: GeorgCoordinator) -> None:
        super().__init__(coordinator, "last_success")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_success
