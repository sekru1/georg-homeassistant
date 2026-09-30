"""Client for the GeORG heating API (``/api/v1/heating``).

Kept free of Home Assistant imports so it can later be extracted into a
standalone package (``pygeorg``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

import aiohttp

API_PATH = "/api/v1"
REQUEST_TIMEOUT = 30

_ROOM_ERROR_KEY = re.compile(r"^rooms\.(\d+)\.id$")

# Marker for "do not send this measurement" (GeORG keeps the old value).
UNSET: Any = object()


class GeorgError(Exception):
    """Base error of the GeORG API client."""


class GeorgConnectionError(GeorgError):
    """GeORG is not reachable or answered unexpectedly."""


class GeorgAuthError(GeorgError):
    """Token missing, invalid, lacking permission or API access disabled (401/403)."""


class GeorgModuleMissingError(GeorgError):
    """The module "Heizungssteuerung" is not booked for the organisation (404)."""


class GeorgValidationError(GeorgError):
    """The request was rejected with 422."""

    def __init__(self, message: str, errors: dict[str, list[str]]) -> None:
        super().__init__(message)
        self.errors = errors


class GeorgUnknownRoomError(GeorgValidationError):
    """One or more reported rooms are unknown or not heating controlled (422)."""

    def __init__(
        self, message: str, errors: dict[str, list[str]], room_ids: list[str]
    ) -> None:
        super().__init__(message, errors)
        self.room_ids = room_ids


@dataclass(frozen=True, slots=True)
class Room:
    """A room with active heating control."""

    id: str
    name: str
    comfort_temperature: float | None
    eco_temperature: float | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Room:
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            comfort_temperature=_float_or_none(data.get("comfort_temperature")),
            eco_temperature=_float_or_none(data.get("eco_temperature")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "comfort_temperature": self.comfort_temperature,
            "eco_temperature": self.eco_temperature,
        }


@dataclass(frozen=True, slots=True)
class Command:
    """A switching request for a room."""

    state: str  # "on" | "off"
    target_temperature: float | None

    @property
    def is_on(self) -> bool:
        return self.state == "on"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Command:
        state = str(data["state"])
        if state not in ("on", "off"):
            raise ValueError(f"Invalid command state: {state}")
        return cls(
            state=state,
            target_temperature=_float_or_none(data.get("target_temperature")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "target_temperature": self.target_temperature}


@dataclass(frozen=True, slots=True)
class Window:
    """The running or next heating window of a room (informative)."""

    name: str
    starts_at: datetime
    ends_at: datetime

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Window:
        return cls(
            name=str(data.get("name") or ""),
            starts_at=datetime.fromisoformat(data["starts_at"]),
            ends_at=datetime.fromisoformat(data["ends_at"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class RoomReport:
    """Measurements of a room sent to ``/heating/sync``.

    ``None`` clears the value in GeORG, ``UNSET`` leaves it untouched.
    """

    id: str
    current_temperature: float | None = UNSET
    humidity: float | None = UNSET

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id}
        if self.current_temperature is not UNSET:
            data["current_temperature"] = self.current_temperature
        if self.humidity is not UNSET:
            data["humidity"] = self.humidity
        return data


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Answer for one room of ``/heating/sync``."""

    id: str
    command: Command | None
    window: Window | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyncResult:
        command = data.get("command")
        window = data.get("window")
        return cls(
            id=str(data["id"]),
            command=Command.from_dict(command) if command else None,
            window=Window.from_dict(window) if window else None,
        )


class GeorgClient:
    """Minimal async client for the GeORG heating endpoints."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, token: str
    ) -> None:
        self._session = session
        self._base_url = normalize_url(base_url)
        self._token = token

    @property
    def base_url(self) -> str:
        return self._base_url

    async def async_get_rooms(self) -> list[Room]:
        """Return all rooms with active heating control."""
        data = await self._request("GET", "/heating/rooms")
        try:
            return [Room.from_dict(item) for item in data["data"]]
        except (KeyError, TypeError, ValueError) as err:
            raise GeorgConnectionError(f"Invalid response: {err}") from err

    async def async_sync(
        self, rooms: list[RoomReport], *, force: bool = False
    ) -> list[SyncResult]:
        """Report measurements and fetch switching requests."""
        payload = {"force": force, "rooms": [room.as_dict() for room in rooms]}
        try:
            data = await self._request("POST", "/heating/sync", payload)
        except GeorgValidationError as err:
            room_ids = _unknown_rooms(err.errors, rooms)
            if room_ids:
                raise GeorgUnknownRoomError(str(err), err.errors, room_ids) from err
            raise
        try:
            return [SyncResult.from_dict(item) for item in data["data"]]
        except (KeyError, TypeError, ValueError) as err:
            raise GeorgConnectionError(f"Invalid response: {err}") from err

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.request(
                    method,
                    f"{self._base_url}{API_PATH}{path}",
                    headers=headers,
                    json=payload,
                )
                body = await _json_or_none(response)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise GeorgConnectionError(f"GeORG not reachable: {err}") from err

        message = ""
        if isinstance(body, dict):
            message = str(body.get("message") or "")

        status = response.status
        if status in (401, 403):
            raise GeorgAuthError(message or f"HTTP {status}")
        if status == 404:
            raise GeorgModuleMissingError(message or "HTTP 404")
        if status == 422:
            errors = body.get("errors") if isinstance(body, dict) else None
            raise GeorgValidationError(message or "HTTP 422", errors or {})
        if status >= 400:
            raise GeorgConnectionError(message or f"HTTP {status}")
        if not isinstance(body, dict):
            raise GeorgConnectionError("Invalid response: no JSON object")
        return body


def normalize_url(url: str) -> str:
    """Strip whitespace, trailing slashes and an accidentally pasted API path."""
    url = url.strip().rstrip("/")
    if url.endswith(API_PATH):
        url = url[: -len(API_PATH)]
    return url


def _unknown_rooms(errors: dict[str, list[str]], rooms: list[RoomReport]) -> list[str]:
    room_ids: list[str] = []
    for key in errors:
        if (match := _ROOM_ERROR_KEY.match(key)) is None:
            continue
        index = int(match.group(1))
        if index < len(rooms) and rooms[index].id not in room_ids:
            room_ids.append(rooms[index].id)
    return room_ids


async def _json_or_none(response: aiohttp.ClientResponse) -> Any:
    try:
        return await response.json(content_type=None)
    except (ValueError, aiohttp.ContentTypeError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
