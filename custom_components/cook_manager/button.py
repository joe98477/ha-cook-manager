"""Buttons: start, stop and next stage for each slot."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CookManagerConfigEntry
from .entity import SlotEntity
from .manager import CookManager, Slot


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    entities: list[ButtonEntity] = []
    for slot in manager.slots:
        entities.extend(
            [
                CookButton(manager, slot, "start"),
                CookButton(manager, slot, "stop"),
                CookButton(manager, slot, "next_stage"),
            ]
        )
    async_add_entities(entities)


class CookButton(SlotEntity, ButtonEntity):
    def __init__(self, manager: CookManager, slot: Slot, key: str) -> None:
        super().__init__(manager, slot, key)
        self._key = key

    async def async_press(self) -> None:
        if self._key == "start":
            await self.manager.async_start(self.slot)
        elif self._key == "stop":
            await self.manager.async_stop(self.slot)
        else:
            await self.manager.async_next_stage(self.slot)
