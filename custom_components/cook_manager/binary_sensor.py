"""Binary sensors: is anything cooking, and per-slot attention flags."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CookManagerConfigEntry
from .engine import CookStatus
from .entity import HubEntity, SlotEntity
from .manager import CookManager, Slot


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    entities: list[BinarySensorEntity] = [CookingSensor(manager)]
    entities.extend(AttentionSensor(manager, slot) for slot in manager.slots)
    async_add_entities(entities)


class CookingSensor(HubEntity, BinarySensorEntity):
    """On while any cook is running. Use it to show the cook view on a dashboard."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, manager: CookManager) -> None:
        super().__init__(manager, "cooking")

    @property
    def is_on(self) -> bool:
        return bool(self.manager.active_slots())


class AttentionSensor(SlotEntity, BinarySensorEntity):
    """On when the cook wants someone: done, probe lost, or grill out of its band."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "attention")

    @property
    def is_on(self) -> bool:
        cook = self.slot.cook
        if cook is None or cook.status == CookStatus.IDLE:
            return False
        if cook.status == CookStatus.DONE:
            return True
        return cook.probe_lost or cook.grill_state in ("low", "high")

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        cook = self.slot.cook
        reason: str | None = None
        if cook is not None and cook.status != CookStatus.IDLE:
            if cook.status == CookStatus.DONE:
                reason = "done"
            elif cook.probe_lost:
                reason = "probe_lost"
            elif cook.grill_state in ("low", "high"):
                reason = f"grill_{cook.grill_state}"
        return {"reason": reason}
