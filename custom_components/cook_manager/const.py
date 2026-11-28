"""Constants for Cook Manager."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "cook_manager"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

CONF_SLOTS = "slots"
CONF_PROBES = "probes"
CONF_CONTROLLERS = "grill_controllers"
CONF_NOTIFY = "notify_services"
CONF_IGNORED = "ignored_readings"
CONF_PRE_ALERT = "pre_alert"
CONF_GRILL_TOLERANCE = "grill_tolerance"
CONF_IKAMAND = "ikamand_hosts"
CONF_ALERTS = "alerts"
CONF_NOTIFY_URL = "notify_url"
CONF_STALE = "stale_minutes"
CONF_LINKS = "link_sensors"

# Alert switches -> the cook events each one covers. Every event is still fired on the bus.
ALERT_GROUPS: dict[str, tuple[str, ...]] = {
    "approaching": ("approaching",),
    "target_reached": ("target_reached",),
    "stage_started": ("stage_started",),
    "done": ("done",),
    "grill_ready": ("grill_ready",),
    "grill_band": ("grill_low", "grill_high"),
    "grill_recovered": ("grill_recovered",),
    "probe": ("probe_lost", "probe_restored"),
    "stalled": ("stalled",),
}
DEFAULT_ALERTS = [key for key in ALERT_GROUPS if key not in ("grill_recovered", "stalled")]
# Alerts that should get through a silenced phone.
URGENT_EVENTS = ("target_reached", "done", "probe_lost")

NOTIFY_STARTER = "starter"
NOTIFY_ALL = "all"

IKAMAND_PREFIX = "ikamand:"
IKAMAND_CHANNELS = ("pit", "p1", "p2", "p3")
IKAMAND_POLL_SECONDS = 5

DEFAULT_SLOTS = 4
DEFAULT_IGNORED = "-1"
DEFAULT_PRE_ALERT = 5.0
# Minutes without a report before a sensor probe counts as having no reading. 0 = never.
DEFAULT_STALE = 0
# A link sensor may drop for this long before its probes count as gone. Some integrations
# connect in short sessions, so their "connected" sensor goes off between sessions.
LINK_GRACE_SECONDS = 90
# Link sensors of these device classes mean "docked": on = the probe is in its charger.
LINK_DOCKED_CLASSES = ("battery_charging", "plug")
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

CARDS_FILE = "cook-manager-cards.js"
CARDS_URL = f"/{DOMAIN}/{CARDS_FILE}"

SERVICE_START = "start_cook"
SERVICE_STOP = "stop_cook"
SERVICE_NEXT_STAGE = "next_stage"
SERVICE_RELOAD_RECIPES = "reload_recipes"
SERVICE_CONFIGURE = "configure_cook"
