"""Constants for Cook Manager."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "cook_manager"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.TEXT,
]

CONF_SLOTS = "slots"
CONF_PROBES = "probes"
CONF_CONTROLLERS = "grill_controllers"
CONF_NOTIFY = "notify_services"
CONF_IGNORED = "ignored_readings"
CONF_PRE_ALERT = "pre_alert"
CONF_GRILL_TOLERANCE = "grill_tolerance"

DEFAULT_SLOTS = 4
DEFAULT_IGNORED = "-1"
DEFAULT_PRE_ALERT = 5.0
DEFAULT_GRILL_TOLERANCE = 15.0
DEFAULT_TARGET = 63.0
DEFAULT_GRILL_TARGET = 120.0

OPTION_NONE = "None"
OPTION_MANUAL = "Manual"
OPTION_STANDARD = "Standard"

SIGNAL_UPDATE = f"{DOMAIN}_update"
EVENT_COOK = f"{DOMAIN}_event"

STORAGE_VERSION = 1
STORAGE_KEY = DOMAIN
HISTORY_LIMIT = 500
TICK_SECONDS = 15
USER_RECIPE_DIR = "cook_manager/recipes"
COOK_RECORD_DIR = "cook_manager/cooks"

SERVICE_START = "start_cook"
SERVICE_STOP = "stop_cook"
SERVICE_NEXT_STAGE = "next_stage"
SERVICE_RELOAD_RECIPES = "reload_recipes"
