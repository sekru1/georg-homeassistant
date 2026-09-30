"""Tests for the GeORG config and options flow."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.georg.const import (
    CONF_FAILSAFE_GRACE,
    CONF_HEATERS,
    CONF_ROOMS,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .conftest import ROOMS_URL, SYNC_URL, TOKEN, URL, mock_rooms, sync_response


async def test_user_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_rooms(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: f"{URL}/api/v1/", CONF_TOKEN: f" {TOKEN} "},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "georg.example.org"
    assert result["data"] == {CONF_URL: URL, CONF_TOKEN: TOKEN}


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 401}, "invalid_auth"),
        ({"status": 403}, "invalid_auth"),
        ({"status": 404, "json": {"message": "Modul nicht gebucht"}}, "module_missing"),
        ({"status": 404, "text": "<html>Not Found</html>"}, "cannot_connect"),
        ({"exc": TimeoutError}, "cannot_connect"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    kwargs: dict[str, Any],
    error: str,
) -> None:
    aioclient_mock.get(ROOMS_URL, **kwargs)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL, CONF_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_user_flow_requires_https(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://georg.example.org", CONF_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "https_required"}
    assert aioclient_mock.call_count == 0


async def test_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    mock_rooms(aioclient_mock)
    aioclient_mock.post(SYNC_URL, json=sync_response())
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "13|new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_TOKEN] == "13|new"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_options_flow(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_URL: URL, CONF_TOKEN: TOKEN}, options={}
    )
    entry.add_to_hass(hass)
    mock_rooms(aioclient_mock)
    aioclient_mock.post(SYNC_URL, json=sync_response())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "select_room"}
    )
    assert result["step_id"] == "select_room"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"room": "gemeindesaal"}
    )
    assert result["step_id"] == "room"
    assert result["description_placeholders"] == {"room": "Gemeindesaal"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HEATERS: ["climate.saal", "switch.saal"]}
    )
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 60, CONF_FAILSAFE_GRACE: 15}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "save"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.options[CONF_SCAN_INTERVAL] == 60
    assert entry.options[CONF_FAILSAFE_GRACE] == 15
    assert entry.options[CONF_ROOMS] == {
        "gemeindesaal": {
            "name": "Gemeindesaal",
            CONF_HEATERS: ["climate.saal", "switch.saal"],
            "temperature_sensor": None,
            "humidity_sensor": None,
        }
    }
    # The reload reports the newly mapped room with force.
    assert hass.states.get("binary_sensor.gemeindesaal_heating") is not None
    await hass.config_entries.async_unload(entry.entry_id)
