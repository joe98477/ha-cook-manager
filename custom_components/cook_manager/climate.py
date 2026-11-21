"""Thermostat for an iKamand that Cook Manager talks to directly."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CookManagerConfigEntry
from .entity import IkamandEntity
from .ikamand import MAX_TARGET, MIN_TARGET, Ikamand
from .manager import CookManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    async_add_entities(IkamandClimate(manager, device) for device in manager.ikamands.values())


class IkamandClimate(IkamandEntity, ClimateEntity):
    """Heat holds the pit target with the fan; off stops the fan."""

    _attr_name = None  # the main entity of the device: "iKamand"
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    )
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TARGET
    _attr_max_temp = MAX_TARGET
    _attr_target_temperature_step = 1

    def __init__(self, manager: CookManager, device: Ikamand) -> None:
        super().__init__(manager, device, "climate")

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.device.running else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        if not self.device.running:
            return HVACAction.OFF
        return HVACAction.HEATING if self.device.fan else HVACAction.IDLE

    @property
    def native_current_temperature(self) -> float | None:
        return self.device.temperature("pit")

    @property
    def native_target_temperature(self) -> float:
        if self.device.wanted_target is not None:
            return self.device.wanted_target
        return self.device.last_target

    if not hasattr(ClimateEntity, "native_current_temperature"):
        # Home Assistant before 2026.11 knows these under their old names.
        _attr_temperature_unit = UnitOfTemperature.CELSIUS
        current_temperature = native_current_temperature
        target_temperature = native_target_temperature

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "fan": self.device.fan,
            "fire_it_up": self.device.boosting,
            "firmware": self.device.firmware,
            "mac": self.device.mac,
            "host": self.device.host,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        mode = kwargs.get("hvac_mode")
        if temperature is not None:
            if self.device.running or mode == HVACMode.HEAT:
                await self.device.async_set_target(temperature)
            else:
                # Off: remember the target for when it is turned on.
                self.device.last_target = max(MIN_TARGET, min(MAX_TARGET, round(temperature)))
        if mode == HVACMode.OFF:
            await self.device.async_stop()
        self.manager.async_refresh_entities()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.HEAT:
            await self.device.async_set_target(self.device.last_target)
        else:
            await self.device.async_stop()
        self.manager.async_refresh_entities()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
