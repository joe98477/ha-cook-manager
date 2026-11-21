"""Who gets a cook's alerts, and what a phone is sent."""

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.cook_manager.const import DOMAIN

TEMP = {"unit_of_measurement": "°C", "device_class": "temperature"}


def add_person(hass: HomeAssistant, phones: MockConfigEntry, key: str, name: str, user_id: str) -> None:
    """A person with one phone that runs the Home Assistant app."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=phones.entry_id, identifiers={("mobile_app", key)}, name=f"{name} Phone"
    )
    tracker = er.async_get(hass).async_get_or_create(
        "device_tracker", "mobile_app", key, device_id=device.id, suggested_object_id=f"{key}_phone"
    )
    hass.states.async_set(
        f"person.{key}", "home", {"friendly_name": name, "user_id": user_id, "device_trackers": [tracker.entity_id]}
    )


async def setup(hass: HomeAssistant, **options) -> tuple[MockConfigEntry, dict[str, list]]:
    phones = MockConfigEntry(domain="mobile_app", title="Phones")
    phones.add_to_hass(hass)
    add_person(hass, phones, "zach", "Zach", "user-zach")
    add_person(hass, phones, "joe", "Joe", "user-joe")
    calls = {
        "zach": async_mock_service(hass, "notify", "mobile_app_zach_phone"),
        "joe": async_mock_service(hass, "notify", "mobile_app_joe_phone"),
        "house": async_mock_service(hass, "notify", "house"),
    }
    hass.states.async_set("sensor.probe_a", "20", TEMP)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Cook Manager",
        data={},
        options={
            "slots": 2,
            "probes": ["sensor.probe_a"],
            "notify_services": ["notify.house"],
            "notify_url": "/lovelace/bbq",
            "pre_alert": 5,
            "grill_tolerance": 15,
            **options,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, calls


async def reach_target(hass: HomeAssistant, manager, clock: dict) -> None:
    for _ in range(4):
        clock["now"] += 15
        hass.states.async_set("sensor.probe_a", "71", TEMP)
        await manager._async_tick()
    await hass.async_block_till_done()


async def test_alerts_go_to_whoever_started_the_cook(hass: HomeAssistant) -> None:
    entry, calls = await setup(hass)
    manager = entry.runtime_data
    attributes = hass.states.get("sensor.cook_1").attributes
    assert attributes["notify"] == ["starter"]
    assert attributes["notify_summary"] == "Whoever starts the cook"
    assert attributes["notify_choices"] == [
        ["starter", "Whoever starts the cook"], ["all", "Everyone"], ["person.joe", "Joe"], ["person.zach", "Zach"]
    ]
    assert attributes["alerts"] == ["approaching", "target_reached", "stage_started", "done", "grill_ready", "grill_band", "probe"]
    clock = {"now": 1_000_000.0}
    with patch("custom_components.cook_manager.manager.time.time", side_effect=lambda: clock["now"]):
        await hass.services.async_call(
            DOMAIN,
            "start_cook",
            {"slot": 1, "name": "Chicken", "probe": "sensor.probe_a", "target": 70},
            blocking=True,
            context=Context(user_id="user-zach"),
        )
        assert hass.states.get("sensor.cook_1").attributes["notify_summary"] == "Zach"
        await reach_target(hass, manager, clock)

        assert calls["joe"] == [] and calls["house"] == []
        # No recipe, so no rest: "reached" is followed at once by "done", on the same notification.
        reached, done = (call.data for call in calls["zach"][-2:])
        assert done["message"] == "Done" and done["data"]["tag"] == "cook_manager_1"
        assert reached["title"] == "Chicken"
        assert reached["message"] == "Reached 71°, take it off"
        assert reached["data"]["tag"] == "cook_manager_1"
        assert reached["data"]["channel"] == "Cook alerts"
        assert reached["data"]["push"] == {"interruption-level": "time-sensitive"}
        assert reached["data"]["clickAction"] == "/lovelace/bbq"

        # Stopping the cook takes the notification off the phone.
        await hass.services.async_call(DOMAIN, "stop_cook", {"slot": 1}, blocking=True)
        await hass.async_block_till_done()
        assert calls["zach"][-1].data == {"message": "clear_notification", "data": {"tag": "cook_manager_1"}}

        # The Start button knows who pressed it too.
        # Entity services check the user, so this one has to be a real user.
        joe = await hass.auth.async_create_user("Joe", group_ids=["system-admin"])
        state = hass.states.get("person.joe")
        hass.states.async_set("person.joe", "home", {**state.attributes, "user_id": joe.id})
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.cook_1_start"}, blocking=True, context=Context(user_id=joe.id)
        )
        assert manager.slots[0].started_by == joe.id
        await reach_target(hass, manager, clock)
        assert [call.data["message"] for call in calls["joe"]] == ["Reached 71°, take it off", "Done"]


async def test_everyone_one_person_and_fallback(hass: HomeAssistant) -> None:
    entry, calls = await setup(hass)
    manager = entry.runtime_data
    clock = {"now": 1_000_000.0}
    with patch("custom_components.cook_manager.manager.time.time", side_effect=lambda: clock["now"]):
        # Started by an automation (no user): the house list.
        await hass.services.async_call(
            DOMAIN, "start_cook", {"slot": 1, "probe": "sensor.probe_a", "target": 70}, blocking=True
        )
        await reach_target(hass, manager, clock)
        assert [call.data for call in calls["house"]] == [
            {"title": "Cook 1", "message": "Reached 71°, take it off"},
            {"title": "Cook 1", "message": "Done"},
        ]
        assert calls["zach"] == [] and calls["joe"] == []
        await hass.services.async_call(DOMAIN, "stop_cook", {"slot": 1}, blocking=True)

        # One named person, whoever starts it.
        await hass.services.async_call(DOMAIN, "configure_cook", {"slot": 1, "notify": ["person.joe"]}, blocking=True)
        await hass.services.async_call(
            DOMAIN,
            "start_cook",
            {"slot": 1, "probe": "sensor.probe_a", "target": 70},
            blocking=True,
            context=Context(user_id="user-zach"),
        )
        await reach_target(hass, manager, clock)
        assert calls["joe"][-1].data["data"]["tag"] == "cook_manager_1"
        assert calls["zach"] == []
        await hass.services.async_call(DOMAIN, "stop_cook", {"slot": 1}, blocking=True)
        await hass.async_block_till_done()

        # Several at once: the starter, another person and the house list, each told once.
        for key in calls:
            calls[key].clear()
        await hass.services.async_call(
            DOMAIN, "configure_cook", {"slot": 1, "notify": ["starter", "person.joe", "all"]}, blocking=True
        )
        await hass.services.async_call(
            DOMAIN,
            "start_cook",
            {"slot": 1, "probe": "sensor.probe_a", "target": 70},
            blocking=True,
            context=Context(user_id="user-zach"),
        )
        await reach_target(hass, manager, clock)
        assert [len(calls[key]) for key in ("zach", "joe", "house")] == [2, 2, 2]


async def test_alert_switches_for_one_cook(hass: HomeAssistant) -> None:
    entry, calls = await setup(hass)
    manager = entry.runtime_data
    await hass.services.async_call(DOMAIN, "configure_cook", {"slot": 1, "alerts": ["target_reached"]}, blocking=True)
    assert hass.states.get("sensor.cook_1").attributes["alerts"] == ["target_reached"]
    assert hass.states.get("sensor.cook_2").attributes["alerts_custom"] is False
    clock = {"now": 1_000_000.0}
    with patch("custom_components.cook_manager.manager.time.time", side_effect=lambda: clock["now"]):
        await hass.services.async_call(
            DOMAIN, "start_cook", {"slot": 1, "probe": "sensor.probe_a", "target": 70}, blocking=True
        )
        await reach_target(hass, manager, clock)
    assert [call.data["message"] for call in calls["house"]] == ["Reached 71°, take it off"]
    await hass.services.async_call(DOMAIN, "configure_cook", {"slot": 1, "alerts": None}, blocking=True)
    assert hass.states.get("sensor.cook_1").attributes["alerts_custom"] is False


async def test_alert_switches(hass: HomeAssistant) -> None:
    """An alert that is switched off is not sent, but still fires as an event."""
    entry, calls = await setup(hass, alerts=["done"])
    manager = entry.runtime_data
    events = []
    hass.bus.async_listen("cook_manager_event", lambda event: events.append(event.data["type"]))
    clock = {"now": 1_000_000.0}
    with patch("custom_components.cook_manager.manager.time.time", side_effect=lambda: clock["now"]):
        await hass.services.async_call(
            DOMAIN, "start_cook", {"slot": 1, "probe": "sensor.probe_a", "target": 70}, blocking=True
        )
        await reach_target(hass, manager, clock)
    assert "target_reached" in events and "done" in events
    assert [call.data["message"] for call in calls["house"]] == ["Done"]
