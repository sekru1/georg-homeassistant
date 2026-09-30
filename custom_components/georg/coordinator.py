"""Coordinator: report measurements to GeORG and apply the switching requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.climate import (
    ATTR_CURRENT_HUMIDITY,
    ATTR_CURRENT_TEMPERATURE,
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .api import (
    UNSET,
    Command,
    GeorgAuthError,
    GeorgClient,
    GeorgError,
    GeorgModuleMissingError,
    GeorgUnknownRoomError,
    Room,
    RoomReport,
    SyncResult,
    Window,
)
from .const import (
    CONF_FAILSAFE_GRACE,
    CONF_HEATERS,
    CONF_HUMIDITY_SENSOR,
    CONF_ROOMS,
    CONF_SCAN_INTERVAL,
    CONF_TEMPERATURE_SENSOR,
    DEFAULT_FAILSAFE_GRACE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUE_CONNECTION_LOST,
    ISSUE_MODULE_MISSING,
    ISSUE_UNKNOWN_ROOM,
    LOGGER,
    OUTAGE_ISSUE_AFTER,
    STORAGE_VERSION,
)
from .controller import async_apply_command, async_apply_failsafe, is_available

type GeorgConfigEntry = ConfigEntry[GeorgCoordinator]

# Value ranges accepted by GeORG; anything outside would reject the whole sync.
_TEMPERATURE_RANGE = (-50.0, 100.0)
_HUMIDITY_RANGE = (0.0, 100.0)


@dataclass(slots=True)
class RoomConfig:
    """Mapping of a GeORG room to Home Assistant entities (options flow)."""

    id: str
    name: str
    heaters: list[str]
    temperature_sensor: str | None
    humidity_sensor: str | None

    @classmethod
    def from_options(cls, room_id: str, data: dict[str, Any]) -> RoomConfig:
        return cls(
            id=room_id,
            name=data.get("name") or room_id,
            heaters=list(data.get(CONF_HEATERS) or []),
            temperature_sensor=data.get(CONF_TEMPERATURE_SENSOR) or None,
            humidity_sensor=data.get(CONF_HUMIDITY_SENSOR) or None,
        )


@dataclass(slots=True)
class RoomState:
    """Last known GeORG state of a room (persisted)."""

    command: Command | None = None
    window: Window | None = None
    received_at: datetime | None = None
    # Heaters the last command could not be applied to yet.
    pending: list[str] = field(default_factory=list)
    # Heating was switched off locally because GeORG was unreachable.
    failsafe: bool = False
    automation: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command.as_dict() if self.command else None,
            "window": self.window.as_dict() if self.window else None,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "pending": self.pending,
            "failsafe": self.failsafe,
            "automation": self.automation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoomState:
        received_at = data.get("received_at")
        return cls(
            command=Command.from_dict(data["command"]) if data.get("command") else None,
            window=Window.from_dict(data["window"]) if data.get("window") else None,
            received_at=dt_util.parse_datetime(received_at) if received_at else None,
            pending=list(data.get("pending") or []),
            failsafe=bool(data.get("failsafe")),
            automation=data.get("automation", True),
        )


class GeorgCoordinator(DataUpdateCoordinator[dict[str, RoomState]]):
    """Periodically calls ``POST /heating/sync`` (concept §10)."""

    config_entry: GeorgConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: GeorgConfigEntry, client: GeorgClient
    ) -> None:
        options = entry.options
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.failsafe_grace = timedelta(
            minutes=options.get(CONF_FAILSAFE_GRACE, DEFAULT_FAILSAFE_GRACE)
        )
        self.room_configs: dict[str, RoomConfig] = {
            room_id: RoomConfig.from_options(room_id, data)
            for room_id, data in (options.get(CONF_ROOMS) or {}).items()
        }
        self.rooms: dict[str, RoomState] = {}
        self.room_info: dict[str, Room] = {}
        self.last_success: datetime | None = None
        self._outage_since: datetime | None = None
        self._excluded: set[str] = set()
        # The first report after start or a changed mapping is always forced.
        self._force = True
        self._rooms_loaded = False
        self._store = create_store(hass, entry.entry_id)

    @property
    def excluded_rooms(self) -> list[str]:
        """Rooms no longer reported because GeORG rejected them."""
        return sorted(self._excluded)

    async def async_load(self) -> None:
        """Restore the persisted state and clear stale room issues."""
        stored = await self._store.async_load() or {}
        for room_id, data in (stored.get("rooms") or {}).items():
            try:
                self.rooms[room_id] = RoomState.from_dict(data)
            except (KeyError, TypeError, ValueError) as err:
                LOGGER.warning("Discarding stored state of room %s: %s", room_id, err)
        for data in stored.get("room_info") or []:
            room = Room.from_dict(data)
            self.room_info[room.id] = room
        if last_success := stored.get("last_success"):
            self.last_success = dt_util.parse_datetime(last_success)
        for room_id in self.room_configs:
            self.rooms.setdefault(room_id, RoomState())

        registry = ir.async_get(self.hass)
        for domain, issue_id in list(registry.issues):
            if domain == DOMAIN and issue_id.startswith(f"{ISSUE_UNKNOWN_ROOM}_"):
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "rooms": {
                    room_id: state.as_dict() for room_id, state in self.rooms.items()
                },
                "room_info": [room.as_dict() for room in self.room_info.values()],
                "last_success": (
                    self.last_success.isoformat() if self.last_success else None
                ),
            }
        )

    async def async_set_automation(self, room_id: str, enabled: bool) -> None:
        """Enable or disable GeORG control of a room (HA only)."""
        state = self.rooms.setdefault(room_id, RoomState())
        if state.automation == enabled:
            return
        state.automation = enabled
        if enabled:
            # Fetch the current request again, it may have been ignored meanwhile.
            self._force = True
        else:
            state.pending = []
        await self._async_save()
        self.async_update_listeners()
        if enabled:
            await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, RoomState]:
        now = dt_util.utcnow()

        if not self._rooms_loaded:
            await self._async_load_rooms()

        reports = [
            self._measure(config)
            for room_id, config in self.room_configs.items()
            if room_id not in self._excluded
        ]
        if not reports:
            return self.rooms

        force = self._force or any(self.rooms[report.id].failsafe for report in reports)
        try:
            results = await self._async_sync(reports, force)
        except GeorgAuthError as err:
            await self._async_handle_outage(now)
            raise ConfigEntryAuthFailed(str(err)) from err
        except GeorgModuleMissingError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_MODULE_MISSING,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_MODULE_MISSING,
                translation_placeholders={"url": self.client.base_url},
            )
            await self._async_handle_outage(now)
            raise UpdateFailed(str(err)) from err
        except GeorgError as err:
            await self._async_handle_outage(now)
            raise UpdateFailed(str(err)) from err

        await self._async_handle_results(results, now)
        return self.rooms

    async def _async_sync(
        self, reports: list[RoomReport], force: bool
    ) -> list[SyncResult]:
        try:
            return await self.client.async_sync(reports, force=force)
        except GeorgUnknownRoomError as err:
            # GeORG rejects the whole report: drop the affected rooms and retry.
            for room_id in err.room_ids:
                self._exclude_room(room_id)
            await self._async_load_rooms()
            reports = [report for report in reports if report.id not in self._excluded]
            if not reports:
                return []
            return await self.client.async_sync(reports, force=force)

    def _exclude_room(self, room_id: str) -> None:
        LOGGER.error(
            "Room %s is unknown in GeORG or no longer heating controlled", room_id
        )
        self._excluded.add(room_id)
        config = self.room_configs.get(room_id)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{ISSUE_UNKNOWN_ROOM}_{room_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_UNKNOWN_ROOM,
            translation_placeholders={
                "room": config.name if config else room_id,
                "room_id": room_id,
            },
        )

    async def _async_load_rooms(self) -> None:
        """Refresh room names and temperatures; failures are not fatal."""
        try:
            rooms = await self.client.async_get_rooms()
        except GeorgError as err:
            LOGGER.debug("Could not load rooms: %s", err)
            return
        self.room_info = {room.id: room for room in rooms}
        self._rooms_loaded = True

    async def _async_handle_results(
        self, results: list[SyncResult], now: datetime
    ) -> None:
        self._force = False
        self.last_success = now
        self._outage_since = None
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_CONNECTION_LOST)
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_MODULE_MISSING)

        answered: set[str] = set()
        for result in results:
            config = self.room_configs.get(result.id)
            if config is None:
                continue
            answered.add(result.id)
            state = self.rooms.setdefault(result.id, RoomState())
            state.window = result.window
            if result.command is None:
                continue
            LOGGER.debug("Room %s: new request %s", result.id, result.command)
            state.command = result.command
            state.received_at = now
            state.failsafe = False
            state.pending = []
            if state.automation:
                state.pending = await async_apply_command(
                    self.hass, config.heaters, result.command
                )

        # Catch up on heaters that were unavailable when their request arrived.
        for room_id, state in self.rooms.items():
            if room_id in answered and state.received_at == now:
                continue
            await self._async_retry_pending(state)

        await self._async_save()

    async def _async_retry_pending(self, state: RoomState) -> None:
        if not state.pending or state.command is None or not state.automation:
            return
        ready = [entity for entity in state.pending if is_available(self.hass, entity)]
        if not ready:
            return
        failed = await async_apply_command(
            self.hass, ready, state.command, failsafe=state.failsafe
        )
        state.pending = [
            entity
            for entity in state.pending
            if entity not in ready or entity in failed
        ]

    async def _async_handle_outage(self, now: datetime) -> None:
        """GeORG unreachable: raise an issue and switch off overdue rooms."""
        if self._outage_since is None:
            self._outage_since = now
        since = self.last_success or self._outage_since
        if now - since >= OUTAGE_ISSUE_AFTER:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_CONNECTION_LOST,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_CONNECTION_LOST,
                translation_placeholders={"url": self.client.base_url},
            )

        changed = False
        for room_id, config in self.room_configs.items():
            state = self.rooms.get(room_id)
            if (
                state is None
                or not state.automation
                or state.failsafe
                or state.command is None
                or not state.command.is_on
            ):
                continue
            end = state.window.ends_at if state.window else state.received_at
            if end is None or now < end + self.failsafe_grace:
                continue
            room = self.room_info.get(room_id)
            eco = room.eco_temperature if room else None
            LOGGER.warning(
                "GeORG unreachable and heating window of %s ended at %s, "
                "switching off locally",
                config.name,
                end,
            )
            command = Command(state="off", target_temperature=eco)
            state.command = command
            state.failsafe = True
            state.pending = await async_apply_failsafe(
                self.hass, config.heaters, command
            )
            changed = True

        if changed:
            await self._async_save()
            self.async_update_listeners()

    def _measure(self, config: RoomConfig) -> RoomReport:
        """Collect current temperature and humidity of a room."""
        climates = [
            self.hass.states.get(entity)
            for entity in config.heaters
            if entity.startswith(f"{CLIMATE_DOMAIN}.")
        ]
        system_unit = self.hass.config.units.temperature_unit

        temperature: float | None = UNSET
        if config.temperature_sensor:
            state = self.hass.states.get(config.temperature_sensor)
            temperature = _to_celsius(
                _state_float(state.state if state else None),
                state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state else None,
            )
        elif climates:
            temperature = _to_celsius(
                _first_attribute(climates, ATTR_CURRENT_TEMPERATURE), system_unit
            )

        humidity: float | None = UNSET
        if config.humidity_sensor:
            state = self.hass.states.get(config.humidity_sensor)
            humidity = _state_float(state.state if state else None)
        elif (
            climates
            and (value := _first_attribute(climates, ATTR_CURRENT_HUMIDITY)) is not None
        ):
            humidity = value

        return RoomReport(
            id=config.id,
            current_temperature=_in_range(temperature, _TEMPERATURE_RANGE),
            humidity=_in_range(humidity, _HUMIDITY_RANGE),
        )


def create_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store holding the persisted state of a config entry."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


def _state_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_attribute(states: list[Any], attribute: str) -> float | None:
    for state in states:
        if (
            state is not None
            and (value := _state_float(state.attributes.get(attribute))) is not None
        ):
            return value
    return None


def _to_celsius(value: float | None, unit: str | None) -> float | None:
    if value is None or unit in (None, UnitOfTemperature.CELSIUS):
        return value
    try:
        return round(
            TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS), 2
        )
    except Exception:
        return None


def _in_range(value: Any, bounds: tuple[float, float]) -> Any:
    if value is UNSET or value is None:
        return value
    low, high = bounds
    return value if low <= value <= high else None
