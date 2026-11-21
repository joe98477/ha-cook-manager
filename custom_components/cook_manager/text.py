"""Text: the name of the cook in each slot."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
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
    async_add_entities(NameText(manager, slot) for slot in manager.slots)


class NameText(SlotEntity, TextEntity):
    _attr_native_max = 40

    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "name")

    @property
    def native_value(self) -> str:
        return self.slot.name

    async def async_set_value(self, value: str) -> None:
        self.manager.set_name(self.slot, value)
