"""Apply GeORG switching requests to climate and switch entities (concept §4)."""

from __future__ import annotations

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import Command
from .const import LOGGER

# Preferred modes when a thermostat is off and has to heat.
_HEAT_MODES = (HVACMode.HEAT, HVACMode.HEAT_COOL, HVACMode.AUTO)


def is_available(hass: HomeAssistant, entity_id: str) -> bool:
    """Return whether the entity exists and can be controlled."""
    state = hass.states.get(entity_id)
    return state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)


async def async_apply_command(
    hass: HomeAssistant,
    entity_ids: list[str],
    command: Command,
    *,
    failsafe: bool = False,
) -> list[str]:
    """Apply a command to all devices of a room.

    Returns the entities that could not be switched (unavailable or failed);
    the caller retries them once they are available again.
    """
    failed: list[str] = []
    for entity_id in entity_ids:
        if not is_available(hass, entity_id):
            LOGGER.warning(
                "%s is unavailable, request %s is applied later", entity_id, command
            )
            failed.append(entity_id)
            continue
        try:
            await _async_apply_entity(hass, entity_id, command, failsafe)
        except (HomeAssistantError, ValueError) as err:
            LOGGER.warning("Could not apply %s to %s: %s", command, entity_id, err)
            failed.append(entity_id)
    return failed


async def async_apply_failsafe(
    hass: HomeAssistant, entity_ids: list[str], command: Command
) -> list[str]:
    """Switch a room off locally while GeORG is unreachable (concept §5).

    Unlike a regular request, thermostats without a known eco temperature
    are switched off so that no heating can stay on permanently.
    """
    return await async_apply_command(hass, entity_ids, command, failsafe=True)


async def _async_apply_entity(
    hass: HomeAssistant, entity_id: str, command: Command, failsafe: bool
) -> None:
    domain = entity_id.split(".", 1)[0]

    if domain == SWITCH_DOMAIN:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON if command.is_on else SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )
        return

    if domain != CLIMATE_DOMAIN:
        LOGGER.warning("Unsupported heater entity %s", entity_id)
        return

    if command.target_temperature is None:
        if failsafe and not command.is_on:
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: HVACMode.OFF},
                blocking=True,
            )
        # No temperature maintained in GeORG: leave thermostats untouched.
        return

    state = hass.states.get(entity_id)
    if command.is_on and state is not None and state.state == HVACMode.OFF:
        modes = state.attributes.get(ATTR_HVAC_MODES) or []
        mode = next((m for m in _HEAT_MODES if m in modes), None)
        if mode is None:
            mode = next((m for m in modes if m != HVACMode.OFF), None)
        if mode is not None:
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: mode},
                blocking=True,
            )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: command.target_temperature},
        blocking=True,
    )
