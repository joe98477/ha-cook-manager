"""Numbers: meat target and grill target for each slot."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import UnitOfMass, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CookManagerConfigEntry
from .entity import IkamandEntity, SlotEntity
from .ikamand import Ikamand
from .manager import CookManager, Slot


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    entities: list[NumberEntity] = []
    for slot in manager.slots:
        entities.extend(
            [TargetNumber(manager, slot), GrillTargetNumber(manager, slot), WeightNumber(manager, slot)]
        )
    entities.extend(IkamandBoostMinutes(manager, device) for device in manager.ikamands.values())
    async_add_entities(entities)


class IkamandBoostMinutes(IkamandEntity, RestoreNumber):
    """How long Fire it up runs the fan flat out."""

    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 1.0
    _attr_native_max_value = 30.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX

    def __init__(self, manager: CookManager, device: Ikamand) -> None:
        super().__init__(manager, device, "boost_minutes")

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.device.boost_minutes = int(last.native_value)

    @property
    def native_value(self) -> float:
        return self.device.boost_minutes

    async def async_set_native_value(self, value: float) -> None:
        self.device.boost_minutes = int(value)
        self.async_write_ha_state()


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


class WeightNumber(SlotEntity, NumberEntity):
    """Weight of the meat. Optional; 0 means not given. Stored with the cook record."""

    _attr_device_class = NumberDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.KILOGRAMS
    _attr_native_min_value = 0.0
    _attr_native_max_value = 30.0
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX

    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "weight")

    @property
    def native_value(self) -> float:
        return self.slot.weight_kg

    async def async_set_native_value(self, value: float) -> None:
        self.manager.set_weight(self.slot, value)
