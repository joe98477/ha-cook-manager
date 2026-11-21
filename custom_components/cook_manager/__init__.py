"""Cook Manager: run cooks on any temperature probe in Home Assistant."""

from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CARDS_FILE,
    CARDS_URL,
    DOMAIN,
    OPTION_MANUAL,
    PLATFORMS,
    SERVICE_NEXT_STAGE,
    SERVICE_RELOAD_RECIPES,
    SERVICE_START,
    SERVICE_STOP,
)
from .manager import CookManager

type CookManagerConfigEntry = ConfigEntry[CookManager]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ATTR_SLOT = "slot"
ATTR_NAME = "name"
ATTR_PROBE = "probe"
ATTR_GRILL_PROBE = "grill_probe"
ATTR_CONTROLLER = "grill_controller"
ATTR_RECIPE = "recipe"
ATTR_DONENESS = "doneness"
ATTR_TARGET = "target"
ATTR_GRILL_TARGET = "grill_target"
ATTR_WEIGHT = "weight"

_SLOT_SCHEMA = vol.Schema({vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1))})

_START_SCHEMA = _SLOT_SCHEMA.extend(
    {
        vol.Optional(ATTR_NAME): cv.string,
        vol.Optional(ATTR_PROBE): vol.Any(None, cv.string),
        vol.Optional(ATTR_GRILL_PROBE): vol.Any(None, cv.string),
        vol.Optional(ATTR_CONTROLLER): vol.Any(None, cv.string),
        vol.Optional(ATTR_RECIPE): vol.Any(None, cv.string),
        vol.Optional(ATTR_DONENESS): cv.string,
        vol.Optional(ATTR_TARGET): vol.Coerce(float),
        vol.Optional(ATTR_GRILL_TARGET): vol.Coerce(float),
        vol.Optional(ATTR_WEIGHT): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)


def _manager(hass: HomeAssistant) -> CookManager:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("Cook Manager is not set up")
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services."""

    async def start_cook(call: ServiceCall) -> None:
        manager = _manager(hass)
        slot = manager.slot(call.data[ATTR_SLOT])
        data = call.data
        if ATTR_NAME in data:
            manager.set_name(slot, data[ATTR_NAME])
        if ATTR_PROBE in data:
            manager.set_probe(slot, manager.resolve_source(data[ATTR_PROBE]))
        if ATTR_GRILL_PROBE in data:
            manager.set_grill_probe(slot, manager.resolve_source(data[ATTR_GRILL_PROBE]))
        if ATTR_CONTROLLER in data:
            await manager.async_set_controller(slot, manager.resolve_source(data[ATTR_CONTROLLER]))
        if ATTR_RECIPE in data:
            recipe = data[ATTR_RECIPE]
            if recipe in (None, "", OPTION_MANUAL, OPTION_MANUAL.lower()):
                manager.set_recipe(slot, None)
            elif recipe in manager.recipes:
                manager.set_recipe(slot, recipe)
            else:
                raise HomeAssistantError(f"Unknown recipe '{recipe}'")
        if ATTR_DONENESS in data:
            recipe = manager.recipes.get(slot.recipe_id) if slot.recipe_id else None
            wanted = data[ATTR_DONENESS].lower()
            match = next(
                (d for d in (recipe.doneness if recipe else ()) if wanted in (d.id.lower(), d.name.lower())),
                None,
            )
            if match is None:
                raise HomeAssistantError(f"Unknown doneness '{data[ATTR_DONENESS]}' for this recipe")
            manager.set_doneness(slot, match.id)
        if ATTR_TARGET in data:
            manager.set_target(slot, data[ATTR_TARGET])
        if ATTR_GRILL_TARGET in data:
            await manager.async_set_grill_target(slot, data[ATTR_GRILL_TARGET])
        if ATTR_WEIGHT in data:
            manager.set_weight(slot, data[ATTR_WEIGHT])
        await manager.async_start(slot, call.context.user_id)

    async def stop_cook(call: ServiceCall) -> None:
        manager = _manager(hass)
        await manager.async_stop(manager.slot(call.data[ATTR_SLOT]))

    async def next_stage(call: ServiceCall) -> None:
        manager = _manager(hass)
        await manager.async_next_stage(manager.slot(call.data[ATTR_SLOT]))

    async def reload_recipes(call: ServiceCall) -> None:
        await _manager(hass).async_reload_recipes()

    hass.services.async_register(DOMAIN, SERVICE_START, start_cook, schema=_START_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP, stop_cook, schema=_SLOT_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_NEXT_STAGE, next_stage, schema=_SLOT_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RELOAD_RECIPES, reload_recipes)
    await _async_register_cards(hass)
    return True


async def _async_register_cards(hass: HomeAssistant) -> None:
    """Serve the dashboard cards and load them in every browser; nothing to install."""
    path = Path(__file__).parent / "frontend" / CARDS_FILE
    if "frontend" not in hass.config.components or getattr(hass, "http", None) is None:
        return  # no web interface in this installation
    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    if not await hass.async_add_executor_job(path.is_file):
        return
    stamp = int((await hass.async_add_executor_job(path.stat)).st_mtime)
    await hass.http.async_register_static_paths([StaticPathConfig(CARDS_URL, str(path), False)])
    add_extra_js_url(hass, f"{CARDS_URL}?v={stamp}")


async def async_setup_entry(hass: HomeAssistant, entry: CookManagerConfigEntry) -> bool:
    manager = CookManager(hass, entry)
    await manager.async_setup()
    entry.runtime_data = manager
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CookManagerConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_unload()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: CookManagerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
