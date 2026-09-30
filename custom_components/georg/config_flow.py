"""Config, reauth and options flow (room mapping) for GeORG."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import (
    GeorgAuthError,
    GeorgClient,
    GeorgConnectionError,
    GeorgError,
    GeorgModuleMissingError,
    Room,
    is_secure_url,
    normalize_url,
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
    LOGGER,
    MAX_FAILSAFE_GRACE,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import GeorgConfigEntry

CONF_ROOM = "room"

_TOKEN_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


class GeorgConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the connection to a GeORG instance."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: GeorgConfigEntry) -> GeorgOptionsFlow:
        return GeorgOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            token = user_input[CONF_TOKEN].strip()
            errors = await self._async_validate(url, token)
            if not errors:
                return self.async_create_entry(
                    title=urlparse(url).hostname or url,
                    data={CONF_URL: url, CONF_TOKEN: token},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.URL
                            )
                        ),
                        vol.Required(CONF_TOKEN): _TOKEN_SELECTOR,
                    }
                ),
                user_input,
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            errors = await self._async_validate(entry.data[CONF_URL], token)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _TOKEN_SELECTOR}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            token = user_input[CONF_TOKEN].strip()
            errors = await self._async_validate(url, token)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    title=urlparse(url).hostname or url,
                    data_updates={CONF_URL: url, CONF_TOKEN: token},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.URL
                            )
                        ),
                        vol.Required(CONF_TOKEN): _TOKEN_SELECTOR,
                    }
                ),
                {CONF_URL: entry.data[CONF_URL]},
            ),
            errors=errors,
        )

    async def _async_validate(self, url: str, token: str) -> dict[str, str]:
        """Check the token with ``GET /heating/rooms``."""
        if not is_secure_url(url):
            return {CONF_URL: "https_required"}
        client = GeorgClient(async_get_clientsession(self.hass), url, token)
        try:
            await client.async_get_rooms()
        except GeorgAuthError:
            return {"base": "invalid_auth"}
        except GeorgModuleMissingError:
            return {"base": "module_missing"}
        except GeorgConnectionError:
            return {"base": "cannot_connect"}
        except GeorgError:
            LOGGER.exception("Unexpected error while validating GeORG access")
            return {"base": "unknown"}
        return {}


class GeorgOptionsFlow(OptionsFlowWithReload):
    """Map GeORG rooms to heaters and sensors and adjust intervals."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._rooms: dict[str, Room] = {}
        self._room_id: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._options:
            self._options = {
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_FAILSAFE_GRACE: DEFAULT_FAILSAFE_GRACE,
                **self.config_entry.options,
                CONF_ROOMS: dict(self.config_entry.options.get(CONF_ROOMS) or {}),
            }
            client = GeorgClient(
                async_get_clientsession(self.hass),
                self.config_entry.data[CONF_URL],
                self.config_entry.data[CONF_TOKEN],
            )
            try:
                rooms = await client.async_get_rooms()
            except GeorgAuthError:
                return self.async_abort(reason="invalid_auth")
            except GeorgModuleMissingError:
                return self.async_abort(reason="module_missing")
            except GeorgError:
                return self.async_abort(reason="cannot_connect")
            self._rooms = {room.id: room for room in rooms}

        return self.async_show_menu(
            step_id="init", menu_options=["select_room", "settings", "save"]
        )

    async def async_step_select_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._room_id = user_input[CONF_ROOM]
            return await self.async_step_room()

        mapped: dict[str, Any] = self._options[CONF_ROOMS]
        options = [
            selector.SelectOptionDict(
                value=room.id,
                label=f"{room.name} ✓" if room.id in mapped else room.name,
            )
            for room in self._rooms.values()
        ]
        # Mapped rooms GeORG no longer knows, so the mapping can be removed.
        options.extend(
            selector.SelectOptionDict(
                value=room_id, label=f"{data.get('name') or room_id} ⚠"
            )
            for room_id, data in mapped.items()
            if room_id not in self._rooms
        )
        if not options:
            return self.async_abort(reason="no_rooms")

        return self.async_show_form(
            step_id="select_room",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROOM): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._room_id is not None
        room_id = self._room_id
        mapped: dict[str, Any] = self._options[CONF_ROOMS]
        current = mapped.get(room_id, {})
        room = self._rooms.get(room_id)
        name = room.name if room else current.get("name") or room_id

        if user_input is not None:
            mapping = {
                CONF_HEATERS: user_input.get(CONF_HEATERS) or [],
                CONF_TEMPERATURE_SENSOR: user_input.get(CONF_TEMPERATURE_SENSOR),
                CONF_HUMIDITY_SENSOR: user_input.get(CONF_HUMIDITY_SENSOR),
            }
            rooms = dict(mapped)
            if any(mapping.values()):
                rooms[room_id] = {"name": name, **mapping}
            else:
                rooms.pop(room_id, None)
            self._options[CONF_ROOMS] = rooms
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Optional(CONF_HEATERS): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["climate", "switch"], multiple=True
                    )
                ),
                vol.Optional(CONF_TEMPERATURE_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor", device_class="temperature"
                    )
                ),
                vol.Optional(CONF_HUMIDITY_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor", device_class="humidity"
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="room",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            description_placeholders={"room": name},
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._options[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            self._options[CONF_FAILSAFE_GRACE] = int(user_input[CONF_FAILSAFE_GRACE])
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=10,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_FAILSAFE_GRACE): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=MAX_FAILSAFE_GRACE,
                        step=5,
                        unit_of_measurement="min",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(schema, self._options),
        )

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_create_entry(data=self._options)
