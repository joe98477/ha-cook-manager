"""Home Assistant side of Cook Manager: probes, slots, persistence, notifications."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util.unit_conversion import TemperatureConverter
from homeassistant.util.yaml import load_yaml

from .const import (
    CONF_CONTROLLERS,
    COOK_RECORD_DIR,
    CONF_GRILL_TOLERANCE,
    CONF_IGNORED,
    CONF_IKAMAND,
    CONF_NOTIFY,
    CONF_PRE_ALERT,
    CONF_PROBES,
    CONF_SLOTS,
    DEFAULT_GRILL_TARGET,
    DEFAULT_GRILL_TOLERANCE,
    DEFAULT_IGNORED,
    DEFAULT_PRE_ALERT,
    DEFAULT_SLOTS,
    DEFAULT_TARGET,
    EVENT_COOK,
    HISTORY_LIMIT,
    IKAMAND_CHANNELS,
    IKAMAND_POLL_SECONDS,
    IKAMAND_PREFIX,
    OPTION_MANUAL,
    OPTION_NONE,
    OPTION_STANDARD,
    SIGNAL_UPDATE,
    STORAGE_KEY,
    STORAGE_VERSION,
    TICK_SECONDS,
    USER_RECIPE_DIR,
)
from .engine import Cook, CookEvent, CookStatus, Recipe, RecipeError, build_stages, parse_recipe
from .engine.cook import EVENT_DONE, EVENT_STAGE, EVENT_STOPPED
from .ikamand import Ikamand

_LOGGER = logging.getLogger(__name__)

_TEMP_UNITS = (UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.KELVIN)
_MIN_HISTORY_SECONDS = 300


@dataclass
class Slot:
    """One cook slot: what the user has chosen, and the cook when one is running."""

    index: int
    name: str = ""
    probe: str | None = None
    grill_probe: str | None = None
    controller: str | None = None
    recipe_id: str | None = None
    doneness_id: str | None = None
    target: float = DEFAULT_TARGET
    grill_target: float = DEFAULT_GRILL_TARGET
    weight_kg: float = 0.0
    cook: Cook | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "probe": self.probe,
            "grill_probe": self.grill_probe,
            "controller": self.controller,
            "recipe_id": self.recipe_id,
            "doneness_id": self.doneness_id,
            "target": self.target,
            "grill_target": self.grill_target,
            "weight_kg": self.weight_kg,
            "cook": self.cook.as_dict() if self.cook else None,
        }


def parse_ignored(text: str | None) -> list[float]:
    """'-1, 0' -> [-1.0, 0.0]; anything unparseable is skipped."""
    values: list[float] = []
    for part in (text or "").replace(";", ",").split(","):
        try:
            values.append(float(part.strip()))
        except ValueError:
            continue
    return values


def parse_hosts(text: str | None) -> list[str]:
    """'192.168.1.20, http://grill.local/' -> ['192.168.1.20', 'grill.local']."""
    hosts: list[str] = []
    for part in re.split(r"[,;\s]+", text or ""):
        host = part.strip().removeprefix("http://").strip("/")
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def load_recipe_files(builtin: Path, user_dir: Path) -> tuple[dict[str, Recipe], list[str]]:
    """Read built-in and user recipes. Runs in the executor."""
    recipes: dict[str, Recipe] = {}
    problems: list[str] = []
    files: list[tuple[Path, str]] = [(builtin, "built-in")]
    if user_dir.is_dir():
        files.extend((path, "user") for path in sorted(user_dir.glob("*.yaml")))
    for path, source in files:
        try:
            content = load_yaml(str(path))
        except (HomeAssistantError, OSError) as err:
            problems.append(f"{path.name}: {err}")
            continue
        items = content if isinstance(content, list) else [content]
        for item in items:
            try:
                recipe = parse_recipe(item, source=source)
            except RecipeError as err:
                problems.append(f"{path.name}: {err}")
                continue
            recipes[recipe.id] = recipe
    return recipes, problems


def _write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")


class CookManager:
    """Owns the slots and drives the cooks."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        options = {**entry.data, **entry.options}
        self.slot_count = int(options.get(CONF_SLOTS, DEFAULT_SLOTS))
        self.probe_entities: list[str] = list(options.get(CONF_PROBES, []))
        self.controller_entities: list[str] = list(options.get(CONF_CONTROLLERS, []))
        self.notify_services: list[str] = list(options.get(CONF_NOTIFY, []))
        self.pre_alert = float(options.get(CONF_PRE_ALERT, DEFAULT_PRE_ALERT))
        self.grill_tolerance = float(options.get(CONF_GRILL_TOLERANCE, DEFAULT_GRILL_TOLERANCE))
        self._ignored = parse_ignored(options.get(CONF_IGNORED, DEFAULT_IGNORED))

        # iKamands that Cook Manager talks to itself. Their pit and food probes join the
        # probe pool and each one is a grill controller, under ids like "ikamand:<host>:pit".
        session = async_get_clientsession(hass)
        self.ikamands: dict[str, Ikamand] = {
            host: Ikamand(session, host) for host in parse_hosts(options.get(CONF_IKAMAND))
        }
        self.probe_sources: list[str] = [
            *self.probe_entities,
            *(f"{IKAMAND_PREFIX}{host}:{channel}" for host in self.ikamands for channel in IKAMAND_CHANNELS),
        ]
        self.controller_sources: list[str] = [
            *self.controller_entities,
            *(f"{IKAMAND_PREFIX}{host}" for host in self.ikamands),
        ]
        self._polling = False

        self.slots: list[Slot] = []
        self.recipes: dict[str, Recipe] = {}
        self.history: list[dict[str, Any]] = []
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._unsubs: list[Any] = []

    # ----- lifecycle ------------------------------------------------------

    async def async_setup(self) -> None:
        await self.async_reload_recipes()
        stored = await self._store.async_load() or {}
        self.history = list(stored.get("history", []))
        by_index = {item.get("index"): item for item in stored.get("slots", [])}
        for index in range(1, self.slot_count + 1):
            self.slots.append(self._restore_slot(index, by_index.get(index)))

        watched = sorted(set(self.probe_entities) | set(self.controller_entities))
        if watched:
            self._unsubs.append(async_track_state_change_event(self.hass, watched, self._on_source_change))
        self._unsubs.append(
            async_track_time_interval(self.hass, self._async_tick, timedelta(seconds=TICK_SECONDS))
        )
        if self.ikamands:
            # A cook that was running keeps its grill target on the controller.
            for slot in self.slots:
                device = self.ikamand_for(slot.controller)
                if device is not None and slot.cook is not None and slot.cook.status == CookStatus.COOKING:
                    device.want(slot.grill_target)
            self._unsubs.append(
                async_track_time_interval(
                    self.hass, self._async_poll_ikamands, timedelta(seconds=IKAMAND_POLL_SECONDS)
                )
            )
            self.entry.async_create_background_task(
                self.hass, self._async_poll_ikamands(), "cook_manager_ikamand_first_poll"
            )

    async def async_unload(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self._data_to_save())

    def _restore_slot(self, index: int, data: dict[str, Any] | None) -> Slot:
        slot = Slot(index=index)
        if not data:
            return slot
        slot.name = data.get("name") or ""
        slot.probe = data.get("probe")
        slot.grill_probe = data.get("grill_probe")
        slot.controller = data.get("controller")
        slot.recipe_id = data.get("recipe_id") if data.get("recipe_id") in self.recipes else None
        slot.doneness_id = data.get("doneness_id")
        slot.target = float(data.get("target", DEFAULT_TARGET))
        slot.grill_target = float(data.get("grill_target", DEFAULT_GRILL_TARGET))
        slot.weight_kg = float(data.get("weight_kg", 0.0))
        if data.get("cook"):
            try:
                slot.cook = Cook.from_dict(
                    data["cook"], display_temp=self._display_temp, display_delta=self._display_delta
                )
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning("Cook %s could not be restored: %s", index, err)
        return slot

    def _data_to_save(self) -> dict[str, Any]:
        return {"slots": [slot.as_dict() for slot in self.slots], "history": self.history}

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, 30)

    @callback
    def _notify_entities(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    @callback
    def async_refresh_entities(self) -> None:
        self._notify_entities()

    # ----- recipes --------------------------------------------------------

    async def async_reload_recipes(self) -> None:
        builtin = Path(__file__).parent / "recipes" / "builtin.yaml"
        user_dir = Path(self.hass.config.path(USER_RECIPE_DIR))
        recipes, problems = await self.hass.async_add_executor_job(load_recipe_files, builtin, user_dir)
        for problem in problems:
            _LOGGER.warning("Recipe skipped: %s", problem)
        self.recipes = recipes
        self._notify_entities()

    def recipe_labels(self) -> dict[str, str]:
        """Recipe id -> label, unique labels, sorted by category then name."""
        labels: dict[str, str] = {}
        used: set[str] = set()
        for recipe in sorted(self.recipes.values(), key=lambda r: (r.category, r.name.lower())):
            label = recipe.name if recipe.name not in used else f"{recipe.name} ({recipe.id})"
            used.add(label)
            labels[recipe.id] = label
        return labels

    # ----- probes ---------------------------------------------------------

    def _display_temp(self, celsius: float) -> float:
        return TemperatureConverter.convert(
            celsius, UnitOfTemperature.CELSIUS, self.hass.config.units.temperature_unit
        )

    def _display_delta(self, celsius: float) -> float:
        return TemperatureConverter.convert_interval(
            celsius, UnitOfTemperature.CELSIUS, self.hass.config.units.temperature_unit
        )

    def ikamand_for(self, source: str | None) -> Ikamand | None:
        """The iKamand behind a built-in probe or controller id."""
        if not source or not source.startswith(IKAMAND_PREFIX):
            return None
        host = source[len(IKAMAND_PREFIX) :]
        if host not in self.ikamands:
            host = host.rsplit(":", 1)[0]
        return self.ikamands.get(host)

    def _ikamand_label(self, source: str) -> str:
        rest = source[len(IKAMAND_PREFIX) :]
        channel = "" if rest in self.ikamands else rest.rsplit(":", 1)[-1]
        host = rest if not channel else rest.rsplit(":", 1)[0]
        label = "iKamand" if len(self.ikamands) <= 1 else f"iKamand {host}"
        if channel == "pit":
            return f"{label} pit"
        if channel:
            return f"{label} probe {channel[1:]}"
        return label

    def resolve_source(self, value: str | None) -> str | None:
        """Accept one of Cook Manager's own iKamand entities in place of a built-in id."""
        if not value or value.startswith(IKAMAND_PREFIX) or not self.ikamands:
            return value
        entry = er.async_get(self.hass).async_get(value)
        if entry is None or entry.config_entry_id != self.entry.entry_id:
            return value
        for host in self.ikamands:
            prefix = f"{self.entry.entry_id}_ikamand_{host}_"
            if entry.unique_id.startswith(prefix):
                key = entry.unique_id[len(prefix) :]
                if key == "climate":
                    return f"{IKAMAND_PREFIX}{host}"
                if key in IKAMAND_CHANNELS:
                    return f"{IKAMAND_PREFIX}{host}:{key}"
        return value

    async def _async_poll_ikamands(self, _now: datetime | None = None) -> None:
        if self._polling:
            return
        self._polling = True
        try:
            await asyncio.gather(*(device.async_update() for device in self.ikamands.values()))
        finally:
            self._polling = False
        self._notify_entities()

    def reading(self, entity_id: str | None) -> float | None:
        """Current reading of a probe in Celsius, None when there is no usable reading."""
        if not entity_id:
            return None
        if entity_id.startswith(IKAMAND_PREFIX):
            device = self.ikamand_for(entity_id)
            channel = entity_id.rsplit(":", 1)[-1]
            if device is None or channel not in IKAMAND_CHANNELS:
                return None
            return device.temperature(channel)
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        if entity_id.startswith("climate."):
            raw: Any = state.attributes.get("current_temperature")
            unit = self.hass.config.units.temperature_unit
        else:
            raw = state.state
            unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or any(abs(value - ignored) < 0.05 for ignored in self._ignored):
            return None
        if unit not in _TEMP_UNITS:
            unit = self.hass.config.units.temperature_unit
        return TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)

    def _labels(self, entity_ids: list[str]) -> dict[str, str]:
        labels: dict[str, str] = {}
        used: set[str] = set()
        for entity_id in entity_ids:
            if entity_id.startswith(IKAMAND_PREFIX):
                label = self._ikamand_label(entity_id)
            else:
                state = self.hass.states.get(entity_id)
                label = state.name if state is not None else entity_id
            if label in used or label == OPTION_NONE:
                label = f"{label} ({entity_id})"
            used.add(label)
            labels[entity_id] = label
        return labels

    def probe_labels(self) -> dict[str, str]:
        return self._labels(self.probe_sources)

    def controller_labels(self) -> dict[str, str]:
        return self._labels(self.controller_sources)

    def online_probes(self) -> list[str]:
        return [entity_id for entity_id in self.probe_sources if self.reading(entity_id) is not None]

    def probe_options(self, current: str | None) -> list[str]:
        """Choices for a probe select: None, every probe with a reading, and the current one."""
        labels = self.probe_labels()
        options = [OPTION_NONE]
        for entity_id in self.probe_sources:
            if entity_id == current or self.reading(entity_id) is not None:
                options.append(labels[entity_id])
        if current and current not in labels:
            options.append(current)
        return options

    @staticmethod
    def entity_for_label(labels: dict[str, str], label: str) -> str | None:
        if label == OPTION_NONE:
            return None
        for entity_id, known in labels.items():
            if known == label:
                return entity_id
        return label if "." in label or label.startswith(IKAMAND_PREFIX) else None

    # ----- slot settings --------------------------------------------------

    def slot(self, index: int) -> Slot:
        if not 1 <= index <= len(self.slots):
            raise HomeAssistantError(f"There is no cook slot {index}")
        return self.slots[index - 1]

    def set_name(self, slot: Slot, name: str) -> None:
        slot.name = name.strip()
        if slot.cook is not None and slot.cook.active and slot.name:
            slot.cook.name = slot.name
        self._changed()

    def set_probe(self, slot: Slot, entity_id: str | None) -> None:
        slot.probe = entity_id
        if slot.cook is not None and slot.cook.active:
            slot.cook.expects_meat = entity_id is not None
        self._changed()

    def set_grill_probe(self, slot: Slot, entity_id: str | None) -> None:
        slot.grill_probe = entity_id
        self._sync_grill_target(slot)
        self._changed()

    async def async_set_controller(self, slot: Slot, entity_id: str | None) -> None:
        slot.controller = entity_id
        self._sync_grill_target(slot)
        if entity_id and slot.cook is not None and slot.cook.active:
            await self._async_set_controller_temp(slot, slot.grill_target)
        self._changed()

    def set_recipe(self, slot: Slot, recipe_id: str | None) -> None:
        slot.recipe_id = recipe_id
        recipe = self.recipes.get(recipe_id) if recipe_id else None
        if recipe is None:
            slot.doneness_id = None
        else:
            slot.doneness_id = recipe.doneness[0].id if recipe.doneness else None
            target = recipe.default_target()
            if target is not None:
                slot.target = target
            grill = recipe.stages[0].grill_temp if recipe.stages else None
            grill = grill if grill is not None else recipe.grill_temp
            if grill is not None:
                slot.grill_target = grill
        self._changed()

    def set_doneness(self, slot: Slot, doneness_id: str | None) -> None:
        recipe = self.recipes.get(slot.recipe_id) if slot.recipe_id else None
        if recipe is None or not recipe.doneness:
            return
        slot.doneness_id = doneness_id
        target = recipe.target_for(doneness_id)
        if target is not None:
            self.set_target(slot, target)
        self._changed()

    def set_weight(self, slot: Slot, weight_kg: float) -> None:
        slot.weight_kg = weight_kg
        if slot.cook is not None and slot.cook.active:
            slot.cook.weight_kg = weight_kg or None
        self._changed()

    def set_target(self, slot: Slot, target: float) -> None:
        slot.target = target
        if slot.cook is not None and slot.cook.active:
            slot.cook.set_target(target)
        self._changed()

    async def async_set_grill_target(self, slot: Slot, grill_target: float) -> None:
        slot.grill_target = grill_target
        self._sync_grill_target(slot)
        if slot.controller and slot.cook is not None and slot.cook.active:
            await self._async_set_controller_temp(slot, grill_target)
        self._changed()

    def _sync_grill_target(self, slot: Slot) -> None:
        """A running cook watches the grill only when a grill probe or controller is chosen."""
        if slot.cook is None or not slot.cook.active:
            return
        wanted = slot.grill_target if (slot.grill_probe or slot.controller) else None
        slot.cook.set_grill_target(wanted, time.time())

    def doneness_options(self, slot: Slot) -> list[str]:
        recipe = self.recipes.get(slot.recipe_id) if slot.recipe_id else None
        if recipe is None or not recipe.doneness:
            return [OPTION_STANDARD]
        return [item.name for item in recipe.doneness]

    def doneness_label(self, slot: Slot) -> str:
        recipe = self.recipes.get(slot.recipe_id) if slot.recipe_id else None
        if recipe is None or not recipe.doneness:
            return OPTION_STANDARD
        for item in recipe.doneness:
            if item.id == slot.doneness_id:
                return item.name
        return recipe.doneness[0].name

    def recipe_label(self, slot: Slot) -> str:
        if slot.recipe_id and slot.recipe_id in self.recipes:
            return self.recipe_labels()[slot.recipe_id]
        return OPTION_MANUAL

    def _changed(self) -> None:
        self._schedule_save()
        self._notify_entities()

    # ----- cook commands --------------------------------------------------

    async def async_start(self, slot: Slot) -> None:
        if slot.cook is not None and slot.cook.active:
            await self.async_stop(slot)
        recipe = self.recipes.get(slot.recipe_id) if slot.recipe_id else None
        stages = build_stages(recipe)
        watch_grill = bool(slot.grill_probe or slot.controller)
        cook = Cook(
            name=slot.name or (recipe.name if recipe else f"Cook {slot.index}"),
            stages=stages,
            target=slot.target,
            carryover=recipe.carryover if recipe else 0.0,
            grill_target=slot.grill_target if watch_grill else None,
            recipe_id=recipe.id if recipe else None,
            recipe_name=recipe.name if recipe else None,
            doneness=self.doneness_label(slot) if recipe and recipe.doneness else None,
            expects_meat=slot.probe is not None,
            weight_kg=slot.weight_kg or None,
            pre_alert=self.pre_alert,
            grill_tolerance=self.grill_tolerance,
            display_temp=self._display_temp,
            display_delta=self._display_delta,
        )
        slot.cook = cook
        events = cook.start(time.time())
        await self._async_handle_events(slot, events)
        if slot.controller and stages[0].grill_temp is None and cook.grill_target is not None:
            await self._async_set_controller_temp(slot, cook.grill_target)
        self._changed()

    async def async_stop(self, slot: Slot) -> None:
        if slot.cook is None:
            return
        events = slot.cook.stop(time.time())
        await self._async_handle_events(slot, events)
        self._changed()

    async def async_next_stage(self, slot: Slot) -> None:
        if slot.cook is None or not slot.cook.active:
            return
        events = slot.cook.next_stage(time.time())
        await self._async_handle_events(slot, events)
        self._changed()

    # ----- the tick -------------------------------------------------------

    @callback
    def _on_source_change(self, _event: Event[EventStateChangedData]) -> None:
        self._notify_entities()

    async def _async_tick(self, _now: datetime | None = None) -> None:
        now = time.time()
        any_active = False
        for slot in self.slots:
            cook = slot.cook
            if cook is None or not cook.active:
                continue
            any_active = True
            events = cook.update(now, self.reading(slot.probe), self.reading(slot.grill_probe))
            if events:
                await self._async_handle_events(slot, events)
        if any_active:
            self._schedule_save()
        self._notify_entities()

    async def _async_handle_events(self, slot: Slot, events: list[CookEvent]) -> None:
        cook = slot.cook
        if cook is not None:
            cook.log_events(time.time(), events)
        for event in events:
            self.hass.bus.async_fire(
                EVENT_COOK,
                {
                    "slot": slot.index,
                    "name": cook.name if cook else slot.name,
                    "type": event.type,
                    "message": event.message,
                    **event.data,
                },
            )
            if event.type == EVENT_STAGE and event.data.get("grill_temp") is not None:
                slot.grill_target = float(event.data["grill_temp"])
                if slot.controller:
                    await self._async_set_controller_temp(slot, slot.grill_target)
            ended = event.type == EVENT_DONE or (event.type == EVENT_STOPPED and event.data.get("was_active"))
            if ended or (event.type == EVENT_STAGE and event.data.get("rest")):
                await self._async_release_controller(slot)
            if cook is not None and ended:
                await self._async_record_cook(slot, cook)
            if event.notify:
                await self._async_notify(event.message)

    async def _async_record_cook(self, slot: Slot, cook: Cook) -> None:
        """Write the finished cook to its own file and add it to the history index."""
        if cook.started_at is None or cook.finished_at is None:
            return
        if cook.finished_at - cook.started_at < _MIN_HISTORY_SECONDS:
            return
        labels = {**self.probe_labels(), **self.controller_labels()}
        record = cook.record()
        record["sources"] = {
            key: {"entity_id": entity_id, "name": labels.get(entity_id, entity_id)} if entity_id else None
            for key, entity_id in (
                ("probe", slot.probe),
                ("grill_probe", slot.grill_probe),
                ("grill_controller", slot.controller),
            )
        }
        started = datetime.fromtimestamp(cook.started_at)
        slug = re.sub(r"[^a-z0-9]+", "_", cook.name.lower()).strip("_") or "cook"
        filename: str | None = f"{started:%Y%m%d-%H%M}_{slug}.json"
        folder = Path(self.hass.config.path(COOK_RECORD_DIR))
        try:
            await self.hass.async_add_executor_job(_write_record, folder / filename, record)
        except OSError as err:
            _LOGGER.warning("Cook record %s could not be written: %s", filename, err)
            filename = None
        self.history.insert(0, {**cook.summary(), "file": filename})
        del self.history[HISTORY_LIMIT:]

    async def _async_notify(self, message: str) -> None:
        for service in self.notify_services:
            name = service.split(".", 1)[1] if service.startswith("notify.") else service
            if not self.hass.services.has_service("notify", name):
                _LOGGER.warning("Notify service notify.%s does not exist", name)
                continue
            try:
                await self.hass.services.async_call(
                    "notify", name, {"title": "Cook Manager", "message": message}, blocking=False
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Notification through notify.%s failed: %s", name, err)

    async def _async_set_controller_temp(self, slot: Slot, celsius: float) -> None:
        """Hand the grill target to the controller. The controller does its own regulation."""
        if not slot.controller:
            return
        device = self.ikamand_for(slot.controller)
        if device is not None:
            await device.async_set_target(celsius)
            return
        value = round(self._display_temp(celsius))
        try:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": slot.controller, "temperature": value},
                blocking=False,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Could not set %s to %s: %s", slot.controller, value, err)

    async def _async_release_controller(self, slot: Slot) -> None:
        """The meat is off: stop a built-in controller unless another cook still needs it.

        Controllers that are climate entities are left alone; they belong to another integration.
        """
        device = self.ikamand_for(slot.controller)
        if device is None or device.wanted_target is None:
            return
        for other in self.slots:
            if (
                other is not slot
                and other.controller == slot.controller
                and other.cook is not None
                and other.cook.status == CookStatus.COOKING
            ):
                return
        await device.async_stop()

    # ----- summaries for the hub entities ---------------------------------

    def active_slots(self) -> list[Slot]:
        return [slot for slot in self.slots if slot.cook is not None and slot.cook.active]
