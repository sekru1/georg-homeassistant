"""Tests for GeORG diagnostics and entry removal."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.georg.const import DOMAIN

from .conftest import SYNC_URL, TOKEN, URL, mock_rooms, sync_response


async def _setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    mock_rooms(aioclient_mock)
    aioclient_mock.post(
        SYNC_URL,
        json=sync_response(
            {
                "id": "gemeindesaal",
                "command": {"state": "on", "target_temperature": 21.5},
                "window": None,
            }
        ),
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    assert await async_setup_component(hass, "diagnostics", {})
    await _setup(hass, aioclient_mock, config_entry)

    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert result["entry"]["data"] == {"url": URL, "token": "**REDACTED**"}
    assert TOKEN not in str(result)
    assert result["connection"]["last_update_success"] is True
    assert result["excluded_rooms"] == []
    assert [room["id"] for room in result["room_info"]] == ["gemeindesaal", "kapelle"]
    assert result["rooms"]["gemeindesaal"]["command"] == {
        "state": "on",
        "target_temperature": 21.5,
    }
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_remove_entry_deletes_store(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    key = f"{DOMAIN}.{config_entry.entry_id}"
    assert key in hass_storage

    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
