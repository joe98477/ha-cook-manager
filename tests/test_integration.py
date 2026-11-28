"""Home Assistant side: setup screens, entities, a cook end to end."""

import json
from pathlib import Path
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cook_manager.const import DOMAIN, EVENT_COOK

OPTIONS = {
    "slots": 2,
    "probes": ["sensor.probe_a", "sensor.probe_b", "climate.pit"],
    "grill_controllers": ["climate.pit"],
    "notify_services": ["notify.phone"],
    "ignored_readings": "-1",
    "pre_alert": 5,
    "grill_tolerance": 15,
}

TEMP = {"unit_of_measurement": "°C", "device_class": "temperature"}


def set_probes(hass: HomeAssistant, a="20", b="-1.0", pit=25) -> None:
    hass.states.async_set("sensor.probe_a", a, {**TEMP, "friendly_name": "Probe A"})
    hass.states.async_set("sensor.probe_b", b, {**TEMP, "friendly_name": "Probe B"})
    hass.states.async_set("climate.pit", "off", {"current_temperature": pit, "friendly_name": "Pit"})


async def setup(hass: HomeAssistant) -> MockConfigEntry:
    set_probes(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Cook Manager", data={}, options=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.cook_manager.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"slots": 3, "probes": ["sensor.probe_a"], "pre_alert": 5, "grill_tolerance": 15},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["slots"] == 3
    assert result["options"]["probes"] == ["sensor.probe_a"]


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = await setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTIONS, "slots": 1}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("sensor.cook_1") is not None
    assert len(entry.runtime_data.slots) == 1


async def test_entities_and_probe_choices(hass: HomeAssistant) -> None:
    await setup(hass)
    assert hass.states.get("sensor.cook_1").state == "idle"
    assert hass.states.get("binary_sensor.cook_manager_cooking").state == "off"
    # Probe B reads -1.0, which means nothing is plugged in.
    assert hass.states.get("sensor.cook_manager_probes_online").state == "2"
    assert hass.states.get("select.cook_1_probe").attributes["options"] == ["None", "Probe A", "Pit"]
    recipes = hass.states.get("select.cook_1_recipe").attributes["options"]
    assert recipes[0] == "Manual"
    assert "Beef steak" in recipes

    set_probes(hass, b="22.5")
    await hass.async_block_till_done()
    assert "Probe B" in hass.states.get("select.cook_1_probe").attributes["options"]


async def test_recipe_sets_targets(hass: HomeAssistant) -> None:
    await setup(hass)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.cook_1_recipe", "option": "Beef steak"}, blocking=True
    )
    assert hass.states.get("number.cook_1_target").state == "57.0"
    assert hass.states.get("number.cook_1_grill_target").state == "230.0"
    assert hass.states.get("select.cook_1_doneness").state == "Medium rare"
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.cook_1_doneness", "option": "Medium"}, blocking=True
    )
    assert hass.states.get("number.cook_1_target").state == "63.0"


async def test_cook_end_to_end(hass: HomeAssistant) -> None:
    entry = await setup(hass)
    manager = entry.runtime_data
    notify = async_mock_service(hass, "notify", "phone")
    climate = async_mock_service(hass, "climate", "set_temperature")
    events = []
    hass.bus.async_listen(EVENT_COOK, lambda event: events.append(event.data))

    clock = {"now": 1_000_000.0}
    with patch("custom_components.cook_manager.manager.time.time", side_effect=lambda: clock["now"]), patch(
        "custom_components.cook_manager.sensor.time.time", side_effect=lambda: clock["now"]
    ):
        await hass.services.async_call(
            DOMAIN,
            "start_cook",
            {
                "slot": 1,
                "name": "Roast",
                "probe": "sensor.probe_a",
                "grill_probe": "climate.pit",
                "grill_controller": "climate.pit",
                "recipe": "beef_roast",
                "doneness": "medium",
                "weight": 2.4,
            },
            blocking=True,
        )
        await hass.async_block_till_done()
        assert hass.states.get("sensor.cook_1").state == "cooking"
        assert hass.states.get("binary_sensor.cook_manager_cooking").state == "on"
        assert climate[-1].data == {"entity_id": "climate.pit", "temperature": 160}

        # Heat up: one degree a minute on the meat, grill steady at 160.
        meat = 20.0
        while meat < 58.5:
            clock["now"] += 15
            meat += 0.25
            set_probes(hass, a=str(meat), pit=160)
            await manager._async_tick()
        await hass.async_block_till_done()
        assert hass.states.get("sensor.cook_1_time_remaining").state not in ("unknown", "unavailable")
        assert hass.states.get("sensor.cook_1_temperature").state == "58.5"

        # Pull temperature is 63 - 4 = 59.
        for _ in range(4):
            clock["now"] += 15
            set_probes(hass, a="59.5", pit=160)
            await manager._async_tick()
        await hass.async_block_till_done()
        assert hass.states.get("sensor.cook_1").state == "resting"

        clock["now"] += 20 * 60 + 15
        await manager._async_tick()
        await hass.async_block_till_done()
        assert hass.states.get("sensor.cook_1").state == "done"
        assert hass.states.get("binary_sensor.cook_1_attention").state == "on"

        await hass.services.async_call("button", "press", {"entity_id": "button.cook_1_stop"}, blocking=True)
        assert hass.states.get("sensor.cook_1").state == "idle"

    kinds = [event["type"] for event in events]
    assert "approaching" in kinds
    assert "target_reached" in kinds
    assert "done" in kinds
    messages = [call.data["message"] for call in notify]
    assert any("take it off" in message for message in messages)
    assert any("Done" in message for message in messages)
    assert len(manager.history) == 1
    assert manager.history[0]["recipe_id"] == "beef_roast"
    assert manager.history[0]["weight_kg"] == 2.4
    record_file = Path(hass.config.path("cook_manager/cooks")) / manager.history[0]["file"]
    record = json.loads(record_file.read_text(encoding="utf-8"))
    record_file.unlink()
    assert record["format"] == 2
    assert record["columns"] == ["time", "meat", "grill", "controller_target", "controller_output"]
    assert record["controller"]["kind"] == "climate"
    assert len(record["samples"][0]) == 5
    assert record["sources"]["probe"]["entity_id"] == "sensor.probe_a"
    assert len(record["samples"]) >= 70
    assert "target_reached" in [event["type"] for event in record["events"]]


async def test_cook_survives_reload(hass: HomeAssistant) -> None:
    entry = await setup(hass)
    await hass.services.async_call(
        DOMAIN, "start_cook", {"slot": 2, "probe": "sensor.probe_a", "target": 70}, blocking=True
    )
    assert hass.states.get("sensor.cook_2").state == "cooking"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.cook_2").state == "cooking"
    assert hass.states.get("number.cook_2_target").state == "70.0"


async def test_silent_probe_has_no_reading(hass: HomeAssistant, freezer) -> None:
    """A sensor that keeps its last value but stops reporting drops out once the limit is set."""
    set_probes(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Cook Manager", data={}, options={**OPTIONS, "stale_minutes": 3})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    manager = entry.runtime_data
    assert manager.reading("sensor.probe_a") == 20

    freezer.tick(200)
    assert manager.reading("sensor.probe_a") is None
    assert "sensor.probe_a" not in manager.online_probes()
    # A climate entity is not judged by its report time.
    assert manager.reading("climate.pit") == 25

    # The same value reported again counts as a fresh reading.
    hass.states.async_set("sensor.probe_a", "20", {**TEMP, "friendly_name": "Probe A"})
    assert manager.reading("sensor.probe_a") == 20


async def test_silent_probe_limit_off_by_default(hass: HomeAssistant, freezer) -> None:
    entry = await setup(hass)
    freezer.tick(3600)
    assert entry.runtime_data.reading("sensor.probe_a") == 20


def _register(hass: HomeAssistant, device_id: str, domain: str, object_id: str) -> None:
    from homeassistant.helpers import entity_registry as er

    er.async_get(hass).async_get_or_create(
        domain, "test", object_id, suggested_object_id=object_id, device_id=device_id
    )


async def test_link_sensors_hide_probes(hass: HomeAssistant, freezer) -> None:
    """A base that is gone, and a probe in its dock, have no reading."""
    from homeassistant.helpers import device_registry as dr

    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    devices = dr.async_get(hass)
    base = devices.async_get_or_create(config_entry_id=owner.entry_id, identifiers={("test", "base")})
    other = devices.async_get_or_create(config_entry_id=owner.entry_id, identifiers={("test", "other")})
    for domain, object_id in (
        ("sensor", "base_probe_1_internal"),
        ("sensor", "base_probe_1_ambient"),
        ("sensor", "base_probe_2_internal"),
        ("binary_sensor", "base_connected"),
        ("binary_sensor", "base_probe_1_charging"),
        ("binary_sensor", "base_probe_2_connected"),
    ):
        _register(hass, base.id, domain, object_id)
    _register(hass, other.id, "sensor", "other_probe_1")

    probes = [
        "sensor.base_probe_1_internal",
        "sensor.base_probe_1_ambient",
        "sensor.base_probe_2_internal",
        "sensor.other_probe_1",
    ]
    for probe in probes:
        hass.states.async_set(probe, "24", TEMP)
    link = {"device_class": "connectivity"}
    dock = {"device_class": "battery_charging"}
    hass.states.async_set("binary_sensor.base_connected", "on", link)
    hass.states.async_set("binary_sensor.base_probe_1_charging", "off", dock)
    hass.states.async_set("binary_sensor.base_probe_2_connected", "on", link)

    links = [
        "binary_sensor.base_connected",
        "binary_sensor.base_probe_1_charging",
        "binary_sensor.base_probe_2_connected",
    ]
    entry = MockConfigEntry(
        domain=DOMAIN, title="Cook Manager", data={}, options={**OPTIONS, "probes": probes, "link_sensors": links}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    manager = entry.runtime_data
    assert manager.online_probes() == probes

    # Probe 1 goes into its dock: both of its temperatures drop out, probe 2 stays.
    hass.states.async_set("binary_sensor.base_probe_1_charging", "on", dock)
    assert manager.online_probes() == ["sensor.base_probe_2_internal", "sensor.other_probe_1"]
    hass.states.async_set("binary_sensor.base_probe_1_charging", "off", dock)

    # Probe 2 loses its own link; nothing happens until the grace period has passed.
    hass.states.async_set("binary_sensor.base_probe_2_connected", "off", link)
    assert "sensor.base_probe_2_internal" in manager.online_probes()
    freezer.tick(100)
    assert manager.online_probes() == [
        "sensor.base_probe_1_internal",
        "sensor.base_probe_1_ambient",
        "sensor.other_probe_1",
    ]
    hass.states.async_set("binary_sensor.base_probe_2_connected", "on", link)
    assert manager.online_probes() == probes

    # The base's link flaps between short sessions: off for 25 seconds is not "gone".
    hass.states.async_set("binary_sensor.base_connected", "off", link)
    freezer.tick(25)
    assert manager.online_probes() == probes
    # Off for good: every probe of that device goes, the other thermometer is untouched.
    freezer.tick(100)
    assert manager.online_probes() == ["sensor.other_probe_1"]
