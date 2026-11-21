"""Home Assistant side: setup screens, entities, a cook end to end."""

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
    assert any("done" in message for message in messages)
    assert len(manager.history) == 1
    assert manager.history[0]["recipe_id"] == "beef_roast"


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
