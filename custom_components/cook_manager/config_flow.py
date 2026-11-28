"""Setup and options screens for Cook Manager."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    ALERT_GROUPS,
    CONF_ALERTS,
    CONF_NOTIFY_URL,
    DEFAULT_ALERTS,
    CONF_CONTROLLERS,
    CONF_GRILL_TOLERANCE,
    CONF_IGNORED,
    CONF_IKAMAND,
    CONF_LINKS,
    CONF_NOTIFY,
    CONF_PRE_ALERT,
    CONF_PROBES,
    CONF_SLOTS,
    CONF_STALE,
    DEFAULT_GRILL_TOLERANCE,
    DEFAULT_IGNORED,
    DEFAULT_PRE_ALERT,
    DEFAULT_SLOTS,
    DEFAULT_STALE,
    DOMAIN,
)

TITLE = "Cook Manager"


def _schema(hass: HomeAssistant, current: dict[str, Any]) -> vol.Schema:
    notify_services = sorted(
        f"notify.{name}" for name in hass.services.async_services().get("notify", {}) if name != "send_message"
    )
    for chosen in current.get(CONF_NOTIFY, []):
        if chosen not in notify_services:
            notify_services.append(chosen)
    return vol.Schema(
        {
            vol.Required(CONF_SLOTS, default=current.get(CONF_SLOTS, DEFAULT_SLOTS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=8, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_PROBES, default=current.get(CONF_PROBES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    multiple=True,
                    filter=[
                        {"domain": "sensor", "device_class": "temperature"},
                        {"domain": "climate"},
                    ],
                )
            ),
            vol.Optional(CONF_CONTROLLERS, default=current.get(CONF_CONTROLLERS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=True, domain="climate")
            ),
            vol.Optional(CONF_IKAMAND, default=current.get(CONF_IKAMAND, "")): selector.TextSelector(),
            vol.Optional(CONF_NOTIFY, default=current.get(CONF_NOTIFY, [])): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_services,
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_ALERTS, default=current.get(CONF_ALERTS, DEFAULT_ALERTS)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(ALERT_GROUPS),
                    multiple=True,
                    translation_key=CONF_ALERTS,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(CONF_NOTIFY_URL, default=current.get(CONF_NOTIFY_URL, "")): selector.TextSelector(),
            vol.Optional(CONF_IGNORED, default=current.get(CONF_IGNORED, DEFAULT_IGNORED)): selector.TextSelector(),
            vol.Optional(CONF_LINKS, default=current.get(CONF_LINKS, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=True, domain="binary_sensor")
            ),
            vol.Optional(CONF_STALE, default=current.get(CONF_STALE, DEFAULT_STALE)): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=60, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min"
                )
            ),
            vol.Required(
                CONF_PRE_ALERT, default=current.get(CONF_PRE_ALERT, DEFAULT_PRE_ALERT)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=20, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C"
                )
            ),
            vol.Required(
                CONF_GRILL_TOLERANCE, default=current.get(CONF_GRILL_TOLERANCE, DEFAULT_GRILL_TOLERANCE)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5, max=50, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C"
                )
            ),
        }
    )


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    data[CONF_SLOTS] = int(data[CONF_SLOTS])
    data.setdefault(CONF_PROBES, [])
    data.setdefault(CONF_CONTROLLERS, [])
    data.setdefault(CONF_NOTIFY, [])
    data.setdefault(CONF_IGNORED, "")
    data.setdefault(CONF_ALERTS, [])
    data.setdefault(CONF_LINKS, [])
    data[CONF_STALE] = int(data.get(CONF_STALE) or 0)
    data[CONF_NOTIFY_URL] = (data.get(CONF_NOTIFY_URL) or "").strip()
    data[CONF_IKAMAND] = (data.get(CONF_IKAMAND) or "").strip()
    return data


class CookManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """One Cook Manager per Home Assistant."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={}, options=_clean(user_input))
        return self.async_show_form(step_id="user", data_schema=_schema(self.hass, {}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CookManagerOptionsFlow()


class CookManagerOptionsFlow(OptionsFlow):
    """Change probes, grill controllers, notifications and alert settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_clean(user_input))
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(self.hass, current))
