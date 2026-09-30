"""Tests for reporting, switching, failsafe and error handling."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.georg.const import DOMAIN

from .conftest import SYNC_URL, mock_rooms, sync_requests, sync_response

ON = {"state": "on", "target_temperature": 21.5}
OFF = {"state": "off", "target_temperature": 16}


def _window(end: str = "2026-11-10T12:15:00+01:00") -> dict[str, str]:
    return {
        "name": "Gottesdienst",
        "starts_at": "2026-11-10T09:00:00+01:00",
        "ends_at": end,
    }


@pytest.fixture
async def calls(hass: HomeAssistant) -> dict[str, list[ServiceCall]]:
    # Set up the real switch component first (the integration loads it),
    # otherwise it would replace the mocked services.
    assert await async_setup_component(hass, "switch", {})
    return {
        "set_temperature": async_mock_service(hass, "climate", "set_temperature"),
        "set_hvac_mode": async_mock_service(hass, "climate", "set_hvac_mode"),
        "turn_on": async_mock_service(hass, "switch", "turn_on"),
        "turn_off": async_mock_service(hass, "switch", "turn_off"),
    }


@pytest.fixture
def devices(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "climate.saal",
        "off",
        {
            "hvac_modes": ["off", "heat"],
            "current_temperature": 18.7,
            "current_humidity": 55,
        },
    )
    hass.states.async_set("switch.kapelle_heizung", "off")
    hass.states.async_set(
        "sensor.kapelle_temperatur", "15.25", {"unit_of_measurement": "°C"}
    )
    hass.states.async_set("sensor.kapelle_feuchte", "unavailable")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int = 120
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("devices")
async def test_report_and_switch(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    calls: dict[str, list[ServiceCall]],
) -> None:
    mock_rooms(aioclient_mock)
    aioclient_mock.post(
        SYNC_URL,
        json=sync_response(
            {"id": "gemeindesaal", "command": ON, "window": _window()},
            {
                "id": "kapelle",
                "command": {"state": "on", "target_temperature": None},
                "window": None,
            },
        ),
    )
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    assert sync_requests(aioclient_mock) == [
        {
            "force": True,
            "rooms": [
                {"id": "gemeindesaal", "current_temperature": 18.7, "humidity": 55},
                {"id": "kapelle", "current_temperature": 15.25, "humidity": None},
            ],
        }
    ]
    # Thermostat was off: heat mode first, then the temperature.
    assert calls["set_hvac_mode"][0].data == {
        "entity_id": "climate.saal",
        "hvac_mode": "heat",
    }
    assert calls["set_temperature"][0].data == {
        "entity_id": "climate.saal",
        "temperature": 21.5,
    }
    assert calls["turn_on"][0].data == {"entity_id": "switch.kapelle_heizung"}

    assert hass.states.get("binary_sensor.gemeindesaal_heating").state == "on"
    assert hass.states.get("sensor.gemeindesaal_target_temperature").state == "21.5"
    assert (
        hass.states.get("sensor.gemeindesaal_heating_window_event").state
        == "Gottesdienst"
    )
    assert (
        hass.states.get("sensor.gemeindesaal_heating_window_end").state
        == "2026-11-10T11:15:00+00:00"
    )
    assert hass.states.get("binary_sensor.georg_verbindung_connection").state == "on"


@pytest.mark.usefixtures("devices")
async def test_null_command_does_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    calls: dict[str, list[ServiceCall]],
    freezer: FrozenDateTimeFactory,
) -> None:
    mock_rooms(aioclient_mock)
    aioclient_mock.post(
        SYNC_URL,
        json=sync_response(
            {"id": "gemeindesaal", "command": OFF, "window": None},
            {"id": "kapelle", "command": None, "window": None},
        ),
    )
    await _setup(hass, config_entry)
    assert len(calls["set_temperature"]) == 1
    assert calls["set_hvac_mode"] == []  # "off" never touches the mode

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        SYNC_URL,
        json=sync_response(
            {"id": "gemeindesaal", "command": None, "window": None},
            {"id": "kapelle", "command": None, "window": None},
        ),
    )
    await _tick(hass, freezer)
    assert sync_requests(aioclient_mock)[0]["force"] is False
    assert len(calls["set_temperature"]) == 1
    assert calls["turn_on"] == calls["turn_off"] == []
    # The last request is kept for display.
    assert hass.states.get("binary_sensor.gemeindesaal_heating").state == "off"


@pytest.mark.usefixtures("devices")
async def test_unavailable_device_is_caught_up(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    calls: dict[str, list[ServiceCall]],
    freezer: FrozenDateTimeFactory,
) -> None:
    hass.states.async_set("switch.kapelle_heizung", "unavailable")
    mock_rooms(aioclient_mock)
    aioclient_mock.post(
        SYNC_URL,
        json=sync_response({"id": "kapelle", "command": ON, "window": None}),
    )
    await _setup(hass, config_entry)
    assert calls["turn_on"] == []
    assert hass.states.get("binary_sensor.kapelle_heating").attributes[
        "pending_devices"
    ] == ["switch.kapelle_heizung"]

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        SYNC_URL,
        json=sync_response({"id": "kapelle", "command": None, "window": None}),
    )
    hass.states.async_set("switch.kapelle_heizung", "off")
    await _tick(hass, freezer)
    assert calls["turn_on"][0].data == {"entity_id": "switch.kapelle_heizung"}
    assert (
        hass.states.get("binary_sensor.kapelle_heating").attributes["pending_devices"]
        == []
    )


@pytest.mark.usefixtures("devices")
async def test_unknown_room(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    mock_rooms(aioclient_mock)
    responses = iter(
        [
            AiohttpClientMockResponse(
                "POST",
                URL(SYNC_URL),
                status=422,
                json={"message": "x", "errors": {"rooms.1.id": ["unbekannt"]}},
            ),
            AiohttpClientMockResponse(
                "POST",
                URL(SYNC_URL),
                json=sync_response(
                    {"id": "gemeindesaal", "command": None, "window": None}
                ),
            ),
        ]
    )

    async def _respond(*args: Any, **kwargs: Any) -> AiohttpClientMockResponse:
        return next(responses)

    aioclient_mock.post(SYNC_URL, side_effect=_respond)
    await _setup(hass, config_entry)

    requests = sync_requests(aioclient_mock)
    assert [room["id"] for room in requests[1]["rooms"]] == ["gemeindesaal"]
    issue = issue_registry.async_get_issue(DOMAIN, "unknown_room_kapelle")
    assert issue is not None
    assert issue.translation_placeholders["room"] == "Kapelle"
    assert hass.states.get("binary_sensor.georg_verbindung_connection").state == "on"


@pytest.mark.usefixtures("devices")
async def test_auth_error_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    mock_rooms(aioclient_mock, status=401)
    aioclient_mock.post(SYNC_URL, status=401, json={"message": "Unauthenticated."})
    await _setup(hass, config_entry)
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


@pytest.mark.usefixtures("devices")
async def test_module_missing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    mock_rooms(aioclient_mock, status=404)
    aioclient_mock.post(SYNC_URL, status=404)
    await _setup(hass, config_entry)
    assert issue_registry.async_get_issue(DOMAIN, "module_missing") is not None
    assert hass.states.get("binary_sensor.georg_verbindung_connection").state == "off"


@pytest.mark.usefixtures("devices")
async def test_failsafe_and_outage_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    calls: dict[str, list[ServiceCall]],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    now = dt_util.utcnow()
    end = (now + timedelta(minutes=10)).isoformat()
    mock_rooms(aioclient_mock)
    aioclient_mock.post(
        SYNC_URL,
        json=sync_response(
            {"id": "gemeindesaal", "command": ON, "window": _window(end)},
            {"id": "kapelle", "command": ON, "window": _window(end)},
        ),
    )
    await _setup(hass, config_entry)
    assert len(calls["turn_on"]) == 1

    aioclient_mock.clear_requests()
    aioclient_mock.post(SYNC_URL, exc=TimeoutError)

    # Window end + 30 min grace not yet reached: nothing happens.
    await _tick(hass, freezer, 30 * 60)
    assert calls["turn_off"] == []
    assert hass.states.get("binary_sensor.georg_verbindung_connection").state == "off"
    assert hass.states.get("binary_sensor.gemeindesaal_heating").state == "on"

    await _tick(hass, freezer, 11 * 60)
    assert calls["turn_off"][0].data == {"entity_id": "switch.kapelle_heizung"}
    assert calls["set_temperature"][-1].data == {
        "entity_id": "climate.saal",
        "temperature": 16.0,
    }
    state = hass.states.get("binary_sensor.gemeindesaal_heating")
    assert state.state == "off"
    assert state.attributes["failsafe"] is True
    assert issue_registry.async_get_issue(DOMAIN, "connection_lost") is None

    # Only once.
    await _tick(hass, freezer)
    assert len(calls["turn_off"]) == 1

    await _tick(hass, freezer, 12 * 3600)
    assert issue_registry.async_get_issue(DOMAIN, "connection_lost") is not None

    # GeORG is back: forced report, issue gone.
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        SYNC_URL,
        json=sync_response(
            {"id": "gemeindesaal", "command": OFF, "window": None},
            {"id": "kapelle", "command": OFF, "window": None},
        ),
    )
    await _tick(hass, freezer)
    assert sync_requests(aioclient_mock)[0]["force"] is True
    assert issue_registry.async_get_issue(DOMAIN, "connection_lost") is None
    assert (
        hass.states.get("binary_sensor.gemeindesaal_heating").attributes["failsafe"]
        is False
    )


@pytest.mark.usefixtures("devices")
async def test_automation_switch(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    calls: dict[str, list[ServiceCall]],
) -> None:
    mock_rooms(aioclient_mock)
    aioclient_mock.post(
        SYNC_URL,
        json=sync_response({"id": "kapelle", "command": None, "window": None}),
    )
    await _setup(hass, config_entry)

    # switch.turn_off is mocked in this test, so use the coordinator directly.
    coordinator = config_entry.runtime_data
    await coordinator.async_set_automation("kapelle", False)
    assert hass.states.get("switch.kapelle_automation").state == "off"

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        SYNC_URL,
        json=sync_response({"id": "kapelle", "command": ON, "window": None}),
    )
    await coordinator.async_refresh()
    assert calls["turn_on"] == []  # ignored while automation is off

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        SYNC_URL,
        json=sync_response({"id": "kapelle", "command": ON, "window": None}),
    )
    await coordinator.async_set_automation("kapelle", True)
    await hass.async_block_till_done()
    assert sync_requests(aioclient_mock)[0]["force"] is True
    assert calls["turn_on"][0].data == {"entity_id": "switch.kapelle_heizung"}


async def test_state_is_restored(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}",
        "data": {
            "rooms": {
                "gemeindesaal": {
                    "command": ON,
                    "window": _window(),
                    "received_at": "2026-11-10T08:00:00+00:00",
                    "pending": [],
                    "failsafe": False,
                    "automation": False,
                }
            },
            "room_info": [],
            "last_success": "2026-11-10T08:00:00+00:00",
        },
    }
    mock_rooms(aioclient_mock, exc=TimeoutError)
    aioclient_mock.post(SYNC_URL, exc=TimeoutError)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("binary_sensor.gemeindesaal_heating").state == "on"
    assert hass.states.get("switch.gemeindesaal_automation").state == "off"
    assert hass.states.get("sensor.georg_verbindung_last_successful_report").state == (
        "2026-11-10T08:00:00+00:00"
    )
