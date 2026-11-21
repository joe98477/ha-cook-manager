"""Shared entity bases."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATE
from .ikamand import Ikamand
from .manager import CookManager, Slot


class _CookManagerEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: CookManager) -> None:
        self.manager = manager

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class HubEntity(_CookManagerEntity):
    """Entity on the Cook Manager device."""

    def __init__(self, manager: CookManager, key: str) -> None:
        super().__init__(manager)
        entry_id = manager.entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Cook Manager",
            manufacturer="Cook Manager",
            entry_type=DeviceEntryType.SERVICE,
        )


class SlotEntity(_CookManagerEntity):
    """Entity on a cook slot device."""

    def __init__(self, manager: CookManager, slot: Slot, key: str) -> None:
        super().__init__(manager)
        entry_id = manager.entry.entry_id
        self.slot = slot
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_slot{slot.index}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_slot{slot.index}")},
            name=f"Cook {slot.index}",
            manufacturer="Cook Manager",
            model="Cook slot",
            entry_type=DeviceEntryType.SERVICE,
        )


class IkamandEntity(_CookManagerEntity):
    """Entity on an iKamand that Cook Manager talks to directly."""

    def __init__(self, manager: CookManager, device: Ikamand, key: str) -> None:
        super().__init__(manager)
        self.device = device
        self._attr_translation_key = f"ikamand_{key}"
        self._attr_unique_id = f"{manager.entry.entry_id}_ikamand_{device.host}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"ikamand_{device.host}")},
            name="iKamand",
            manufacturer="Kamado Joe",
            model="iKamand",
        )

    @property
    def available(self) -> bool:
        return self.device.online
