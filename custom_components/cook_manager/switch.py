"""Switch: fire it up, the fan flat out for a few minutes to light the charcoal."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CookManagerConfigEntry
from .entity import IkamandEntity
from .ikamand import Ikamand
from .manager import CookManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    async_add_entities(IkamandBoostSwitch(manager, device) for device in manager.ikamands.values())


class IkamandBoostSwitch(IkamandEntity, SwitchEntity):
    """On for the set number of minutes, then back to the cook's grill target, or off."""

    def __init__(self, manager: CookManager, device: Ikamand) -> None:
        super().__init__(manager, device, "boost")

    @property
    def is_on(self) -> bool:
        return self.device.boosting

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.device.async_boost()
        self.manager.async_refresh_entities()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.device.async_end_boost()
        self.manager.async_refresh_entities()
