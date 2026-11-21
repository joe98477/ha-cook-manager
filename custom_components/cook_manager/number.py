"""Numbers: meat target and grill target for each slot."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
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
    entities: list[NumberEntity] = []
    for slot in manager.slots:
        entities.extend([TargetNumber(manager, slot), GrillTargetNumber(manager, slot)])
    async_add_entities(entities)


class _TemperatureNumber(SlotEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX


class TargetNumber(_TemperatureNumber):
    _attr_native_min_value = 20.0
    _attr_native_max_value = 120.0

    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "target")

    @property
    def native_value(self) -> float:
        return self.slot.target

    async def async_set_native_value(self, value: float) -> None:
        self.manager.set_target(self.slot, value)


class GrillTargetNumber(_TemperatureNumber):
    """Used only while the slot has a grill probe or a grill controller."""

    _attr_native_min_value = 50.0
    _attr_native_max_value = 400.0
    _attr_native_step = 5.0

    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "grill_target")

    @property
    def native_value(self) -> float:
        return self.slot.grill_target

    async def async_set_native_value(self, value: float) -> None:
        await self.manager.async_set_grill_target(self.slot, value)
