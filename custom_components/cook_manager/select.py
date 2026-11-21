"""Selects: which probe, grill probe, grill controller, recipe and doneness a slot uses."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CookManagerConfigEntry
from .const import OPTION_MANUAL, OPTION_NONE
from .entity import SlotEntity
from .manager import CookManager, Slot


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CookManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    entities: list[SelectEntity] = []
    for slot in manager.slots:
        entities.extend(
            [
                ProbeSelect(manager, slot, "probe", grill=False),
                ProbeSelect(manager, slot, "grill_probe", grill=True),
                ControllerSelect(manager, slot),
                RecipeSelect(manager, slot),
                DonenessSelect(manager, slot),
                NotifySelect(manager, slot),
            ]
        )
    async_add_entities(entities)


class ProbeSelect(SlotEntity, SelectEntity):
    """Pick from the probes that have a reading right now."""

    def __init__(self, manager: CookManager, slot: Slot, key: str, *, grill: bool) -> None:
        super().__init__(manager, slot, key)
        self._grill = grill

    @property
    def _current(self) -> str | None:
        return self.slot.grill_probe if self._grill else self.slot.probe

    @property
    def options(self) -> list[str]:
        return self.manager.probe_options(self._current)

    @property
    def current_option(self) -> str:
        current = self._current
        if current is None:
            return OPTION_NONE
        return self.manager.probe_labels().get(current, current)

    async def async_select_option(self, option: str) -> None:
        entity_id = self.manager.entity_for_label(self.manager.probe_labels(), option)
        if self._grill:
            self.manager.set_grill_probe(self.slot, entity_id)
        else:
            self.manager.set_probe(self.slot, entity_id)


class ControllerSelect(SlotEntity, SelectEntity):
    """Optional grill controller: a climate entity that is handed the grill target."""

    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "grill_controller")

    @property
    def options(self) -> list[str]:
        labels = self.manager.controller_labels()
        options = [OPTION_NONE, *labels.values()]
        current = self.slot.controller
        if current and current not in labels:
            options.append(current)
        return options

    @property
    def current_option(self) -> str:
        current = self.slot.controller
        if current is None:
            return OPTION_NONE
        return self.manager.controller_labels().get(current, current)

    async def async_select_option(self, option: str) -> None:
        entity_id = self.manager.entity_for_label(self.manager.controller_labels(), option)
        await self.manager.async_set_controller(self.slot, entity_id)


class RecipeSelect(SlotEntity, SelectEntity):
    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "recipe")

    @property
    def options(self) -> list[str]:
        return [OPTION_MANUAL, *self.manager.recipe_labels().values()]

    @property
    def current_option(self) -> str:
        return self.manager.recipe_label(self.slot)

    async def async_select_option(self, option: str) -> None:
        recipe_id = next(
            (rid for rid, label in self.manager.recipe_labels().items() if label == option), None
        )
        self.manager.set_recipe(self.slot, recipe_id)


class DonenessSelect(SlotEntity, SelectEntity):
    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "doneness")

    @property
    def options(self) -> list[str]:
        return self.manager.doneness_options(self.slot)

    @property
    def current_option(self) -> str:
        return self.manager.doneness_label(self.slot)

    async def async_select_option(self, option: str) -> None:
        recipe = self.manager.recipes.get(self.slot.recipe_id) if self.slot.recipe_id else None
        if recipe is None:
            return
        match = next((item for item in recipe.doneness if item.name == option), None)
        if match is not None:
            self.manager.set_doneness(self.slot, match.id)


class NotifySelect(SlotEntity, SelectEntity):
    """Who gets this slot's alerts: whoever starts the cook, everyone, or one person."""

    def __init__(self, manager: CookManager, slot: Slot) -> None:
        super().__init__(manager, slot, "notify")

    @property
    def options(self) -> list[str]:
        options = self.manager.notify_options()
        labels = list(options.values())
        if self.slot.notify not in options:
            labels.append(self.slot.notify)
        return labels

    @property
    def current_option(self) -> str:
        return self.manager.notify_options().get(self.slot.notify, self.slot.notify)

    async def async_select_option(self, option: str) -> None:
        for value, label in self.manager.notify_options().items():
            if label == option:
                self.manager.set_notify(self.slot, value)
                return
