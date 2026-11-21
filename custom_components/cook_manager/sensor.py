"""Sensors: cook status, temperatures, time remaining, progress."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import CookManagerConfigEntry
from .engine import CookStatus
from .const import IKAMAND_CHANNELS
from .entity import HubEntity, IkamandEntity, SlotEntity
from .ikamand import Ikamand
from .manager import CookManager, Slot


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    entities: list[SensorEntity] = [ActiveCooksSensor(manager), ProbesOnlineSensor(manager)]
    for slot in manager.slots:
        entities.extend(
            [
                StatusSensor(manager, slot),
                ProbeTemperatureSensor(manager, slot, "temperature", grill=False),
                ProbeTemperatureSensor(manager, slot, "grill_temperature", grill=True),
                TimeRemainingSensor(manager, slot),
                FinishTimeSensor(manager, slot),
                ProgressSensor(manager, slot),
                StageSensor(manager, slot),
            ]
        )
    for device in manager.ikamands.values():
        entities.extend(IkamandTemperatureSensor(manager, device, channel) for channel in IKAMAND_CHANNELS)
        entities.append(IkamandFanSensor(manager, device))
    async_add_entities(entities)


class IkamandTemperatureSensor(IkamandEntity, SensorEntity):
    """Pit or food probe of an iKamand. Unknown while no probe is plugged in."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, manager: CookManager, device: Ikamand, channel: str) -> None:
        super().__init__(manager, device, channel)
        self._channel = channel

    @property
    def native_value(self) -> float | None:
        return self.device.temperature(self._channel)


class IkamandFanSensor(IkamandEntity, SensorEntity):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, manager: CookManager, device: Ikamand) -> None:
        super().__init__(manager, device, "fan")

    @property
    def native_value(self) -> int | None:
        return self.device.fan


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return dt_util.utc_from_timestamp(timestamp).isoformat()


class StatusSensor(SlotEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [status.value for status in CookStatus]
    _attr_name = None  # the main entity of the slot: "Cook 1"

    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "status")

    @property
    def native_value(self) -> str:
        return self.slot.cook.status.value if self.slot.cook else CookStatus.IDLE.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slot = self.slot
        cook = slot.cook
        attrs: dict[str, Any] = {
            "slot": slot.index,
            "cook_name": (cook.name if cook and cook.status != CookStatus.IDLE else slot.name) or None,
            "probe": slot.probe,
            "grill_probe": slot.grill_probe,
            "grill_controller": slot.controller,
            "recipe": self.manager.recipe_label(slot),
        }
        if cook is None or cook.status == CookStatus.IDLE:
            return attrs
        stage = cook.stage
        attrs.update(
            {
                "doneness": cook.doneness,
                "target": cook.target,
                "pull_temperature": cook.pull_temp,
                "grill_target": cook.grill_target,
                "grill_state": cook.grill_state,
                "stage": stage.name if stage else None,
                "stage_number": cook.stage_index + 1 if stage else None,
                "stage_count": len(cook.stages),
                "stages": [item.name for item in cook.stages],
                "started": _iso(cook.started_at),
                "finished": _iso(cook.finished_at),
                "rate_per_hour": round(cook.estimate.rate_per_hour, 1)
                if cook.estimate.rate_per_hour is not None
                else None,
                "estimate_method": cook.estimate.method,
                "stalled": cook.stalled,
                "probe_lost": cook.probe_lost,
                "max_temperature": cook.max_meat,
            }
        )
        return attrs


class ProbeTemperatureSensor(SlotEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, manager: CookManager, slot: Slot, key: str, *, grill: bool) -> None:
        super().__init__(manager, slot, key)
        self._grill = grill

    @property
    def native_value(self) -> float | None:
        value = self.manager.reading(self.slot.grill_probe if self._grill else self.slot.probe)
        return round(value, 1) if value is not None else None


class TimeRemainingSensor(SlotEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0

    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "time_remaining")

    @property
    def native_value(self) -> int | None:
        cook = self.slot.cook
        if cook is None:
            return None
        remaining = cook.remaining_seconds(time.time())
        return round(remaining / 60) if remaining is not None else None


class FinishTimeSensor(SlotEntity, SensorEntity):
    """Clock time the current phase ends. Held steady unless it moves by over a minute."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "finish_time")
        self._value: datetime | None = None

    @property
    def native_value(self) -> datetime | None:
        cook = self.slot.cook
        remaining = cook.remaining_seconds(time.time()) if cook else None
        if remaining is None:
            self._value = None
            return None
        finish = (dt_util.utcnow() + timedelta(seconds=remaining)).replace(second=0, microsecond=0)
        if self._value is None or abs((finish - self._value).total_seconds()) > 60:
            self._value = finish
        return self._value


class ProgressSensor(SlotEntity, SensorEntity):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 0

    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "progress")

    @property
    def native_value(self) -> float | None:
        cook = self.slot.cook
        if cook is None or cook.status == CookStatus.IDLE:
            return None
        return cook.progress()


class StageSensor(SlotEntity, SensorEntity):
    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "stage")

    @property
    def native_value(self) -> str | None:
        cook = self.slot.cook
        stage = cook.stage if cook else None
        return stage.name if stage else None


class ActiveCooksSensor(HubEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, manager: CookManager) -> None:
        super().__init__(manager, "active_cooks")

    @property
    def native_value(self) -> int:
        return len(self.manager.active_slots())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "cooks": [
                {"slot": slot.index, "name": slot.cook.name, "status": slot.cook.status.value}
                for slot in self.manager.active_slots()
                if slot.cook is not None
            ]
        }


class ProbesOnlineSensor(HubEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, manager: CookManager) -> None:
        super().__init__(manager, "probes_online")

    @property
    def native_value(self) -> int:
        return len(self.manager.online_probes())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        labels = self.manager.probe_labels()
        return {
            "probes": [
                {
                    "entity_id": entity_id,
                    "name": labels[entity_id],
                    "temperature": round(self.manager.reading(entity_id) or 0.0, 1),
                }
                for entity_id in self.manager.online_probes()
            ]
        }
