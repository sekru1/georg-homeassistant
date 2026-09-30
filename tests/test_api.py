"""Tests for the GeORG API client."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.georg.api import (
    GeorgAuthError,
    GeorgClient,
    GeorgConnectionError,
    GeorgModuleMissingError,
    GeorgUnknownRoomError,
    GeorgValidationError,
    RoomReport,
    normalize_url,
)

from .conftest import ROOMS, ROOMS_URL, SYNC_URL, TOKEN, URL


def _client(hass: HomeAssistant) -> GeorgClient:
    return GeorgClient(async_get_clientsession(hass), URL, TOKEN)


def test_normalize_url() -> None:
    assert normalize_url(" https://g.example.org/ ") == "https://g.example.org"
    assert normalize_url("https://g.example.org/api/v1/") == "https://g.example.org"


async def test_get_rooms(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(ROOMS_URL, json=ROOMS)
    rooms = await _client(hass).async_get_rooms()
    assert [room.id for room in rooms] == ["gemeindesaal", "kapelle"]
    assert rooms[0].comfort_temperature == 21.5
    assert rooms[1].eco_temperature is None
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Accept"] == "application/json"


async def test_sync(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        SYNC_URL,
        json={
            "data": [
                {
                    "id": "gemeindesaal",
                    "command": {"state": "on", "target_temperature": 21.5},
                    "window": {
                        "name": "Gottesdienst",
                        "starts_at": "2026-11-10T09:00:00+01:00",
                        "ends_at": "2026-11-10T12:15:00+01:00",
                    },
                },
                {"id": "kapelle", "command": None, "window": None},
            ]
        },
    )
    results = await _client(hass).async_sync(
        [
            RoomReport("gemeindesaal", current_temperature=18.7, humidity=52),
            RoomReport("kapelle", current_temperature=None),
            RoomReport("buero"),
        ],
        force=True,
    )
    assert aioclient_mock.mock_calls[0][2] == {
        "force": True,
        "rooms": [
            {"id": "gemeindesaal", "current_temperature": 18.7, "humidity": 52},
            {"id": "kapelle", "current_temperature": None},
            {"id": "buero"},
        ],
    }
    assert results[0].command.is_on
    assert results[0].command.target_temperature == 21.5
    assert results[0].window.name == "Gottesdienst"
    assert results[0].window.ends_at.isoformat() == "2026-11-10T12:15:00+01:00"
    assert results[1].command is None
    assert results[1].window is None


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, GeorgAuthError),
        (403, GeorgAuthError),
        (404, GeorgModuleMissingError),
        (500, GeorgConnectionError),
    ],
)
async def test_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    error: type[Exception],
) -> None:
    aioclient_mock.get(ROOMS_URL, status=status, json={"message": "nope"})
    with pytest.raises(error):
        await _client(hass).async_get_rooms()


async def test_timeout(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(ROOMS_URL, exc=TimeoutError)
    with pytest.raises(GeorgConnectionError):
        await _client(hass).async_get_rooms()


async def test_unknown_room(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        SYNC_URL,
        status=422,
        json={
            "message": "Raum unbekannt",
            "errors": {"rooms.1.id": ["Raum „kapelle“ ist unbekannt."]},
        },
    )
    with pytest.raises(GeorgUnknownRoomError) as exc:
        await _client(hass).async_sync(
            [RoomReport("gemeindesaal"), RoomReport("kapelle")]
        )
    assert exc.value.room_ids == ["kapelle"]


async def test_other_validation_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        SYNC_URL,
        status=422,
        json={"message": "invalid", "errors": {"rooms.0.humidity": ["too high"]}},
    )
    with pytest.raises(GeorgValidationError) as exc:
        await _client(hass).async_sync([RoomReport("gemeindesaal", humidity=50)])
    assert not isinstance(exc.value, GeorgUnknownRoomError)
