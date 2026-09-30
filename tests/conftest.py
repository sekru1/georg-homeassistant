"""Fixtures for GeORG tests."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_TOKEN, CONF_URL
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.georg.const import CONF_HEATERS, CONF_ROOMS, DOMAIN

URL = "https://georg.example.org"
ROOMS_URL = f"{URL}/api/v1/heating/rooms"
SYNC_URL = f"{URL}/api/v1/heating/sync"
TOKEN = "12|secret"

ROOMS = {
    "data": [
        {
            "id": "gemeindesaal",
            "name": "Gemeindesaal",
            "comfort_temperature": 21.5,
            "eco_temperature": 16,
        },
        {
            "id": "kapelle",
            "name": "Kapelle",
            "comfort_temperature": 20,
            "eco_temperature": None,
        },
    ]
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Enable loading the custom integration."""


@pytest.fixture
def options() -> dict[str, Any]:
    return {
        CONF_ROOMS: {
            "gemeindesaal": {
                "name": "Gemeindesaal",
                CONF_HEATERS: ["climate.saal"],
            },
            "kapelle": {
                "name": "Kapelle",
                CONF_HEATERS: ["switch.kapelle_heizung"],
                "temperature_sensor": "sensor.kapelle_temperatur",
                "humidity_sensor": "sensor.kapelle_feuchte",
            },
        }
    }


@pytest.fixture
def config_entry(options: dict[str, Any]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="georg.example.org",
        data={CONF_URL: URL, CONF_TOKEN: TOKEN},
        options=options,
    )


def sync_response(*rooms: dict[str, Any]) -> dict[str, Any]:
    return {"data": list(rooms)}


def mock_rooms(aioclient_mock: AiohttpClientMocker, **kwargs: Any) -> None:
    kwargs.setdefault("json", ROOMS)
    aioclient_mock.get(ROOMS_URL, **kwargs)


def sync_requests(aioclient_mock: AiohttpClientMocker) -> list[Any]:
    """Return the JSON bodies of all sync requests."""
    return [
        data
        for method, url, data, _headers in aioclient_mock.mock_calls
        if method == "POST" and str(url) == SYNC_URL
    ]
