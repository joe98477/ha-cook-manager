"""The built-in iKamand connection, against a fake device."""

from unittest.mock import patch
from urllib.parse import urlencode

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.cook_manager.const import DOMAIN
from custom_components.cook_manager.ikamand import Ikamand, parse_reply

HOST = "192.0.2.10"
PIT = f"ikamand:{HOST}:pit"
PROBE_1 = f"ikamand:{HOST}:p1"
CONTROLLER = f"ikamand:{HOST}"


class FakeIkamand:
    """Stands in for the device: answers /data and /info, takes /cook."""

    def __init__(self) -> None:
        self.clock = {"now": 1_000_000.0}
        self.reachable = True
        self.posts: list[dict] = []
        self.state = {
            "time": "1000000", "acs": "0", "csid": "", "tpt": "0", "sce": "0", "p": "0", "tft": "0",
            "as": "0", "ct": "0", "dc": "0", "pt": "25", "t1": "400", "t2": "400", "t3": "400",
        }

    def now(self) -> float:
        return self.clock["now"]

    def advance(self, seconds: float) -> None:
        self.clock["now"] += seconds
        self.state["time"] = str(int(self.clock["now"]))

    def power_cycle(self) -> None:
        self.state.update({"time": "120", "acs": "0", "tpt": "0", "sce": "0", "dc": "0"})

    async def get(self, device: Ikamand, path: str):
        if not self.reachable:
            return None
        if path == "info":
            return {"fw_version": "1.0.47", "MAC": "AA:BB:CC:DD:EE:FF"}
        return dict(self.state)

    async def post(self, device: Ikamand, payload: dict) -> bool:
        device._last_post = self.now()
        if not self.reachable:
            return False
        urlencode(payload)
        self.posts.append(dict(payload))
        self.state.update({key: str(payload[key]) for key in ("acs", "tpt", "sce")})
        self.state["time"] = str(payload["ct"])
        self.state["dc"] = "100" if payload["acs"] else "0"
        return True

    def patches(self):
        return (
            patch.object(Ikamand, "_get", autospec=True, side_effect=self.get),
            patch.object(Ikamand, "_post", autospec=True, side_effect=self.post),
            patch("custom_components.cook_manager.ikamand.time.time", side_effect=self.now),
            patch("custom_components.cook_manager.manager.time.time", side_effect=self.now),
            patch("custom_components.cook_manager.sensor.time.time", side_effect=self.now),
        )


def test_parse_reply() -> None:
    """The real device sends two status lines and no headers."""
    raw = b"HTTP/1.0 200 OK\r\nHTTP/1.1 200 OK\r\n\ntime=1795249740&rm=0&acs=0&csid=&cm=0&pt=28&t1=400&dc=0&tpt=0"
    reply = parse_reply(raw)
    assert reply["pt"] == "28" and reply["csid"] == "" and reply["time"] == "1795249740"
    assert parse_reply(b"HTTP/1.0 200 OK\r\nHTTP/1.1 200 OK\r\n\n") == {}
    assert parse_reply(b"HTTP/1.0 404 Not Found\r\n\n") is None
    assert parse_reply(b"") is None


async def test_device_holds_target_and_recovers() -> None:
    fake = FakeIkamand()
    p = fake.patches()
    with p[0], p[1], p[2]:
        device = Ikamand(HOST)
        await device.async_update()
        assert device.online
        assert device.temperature("pit") == 25
        assert device.temperature("p1") is None  # 400 means no probe
        assert device.firmware == "1.0.47"
        assert not device.running

        await device.async_set_target(110)
        assert fake.posts[-1]["acs"] == 1 and fake.posts[-1]["tpt"] == 110
        assert fake.posts[-1]["sce"] == int(fake.now()) + 86400
        assert device.running and device.target == 110

        # Nothing is repeated while the device is on its target.
        fake.advance(60)
        await device.async_update()
        assert len(fake.posts) == 1

        # Power blip: the device forgets the cook; the next poll puts it back.
        fake.advance(60)
        fake.power_cycle()
        await device.async_update()
        assert len(fake.posts) == 2 and fake.posts[-1]["tpt"] == 110

        # The device's 24 hour cook limit is renewed before it runs out.
        fake.advance(86400 - 1800)
        await device.async_update()
        assert len(fake.posts) == 3
        assert fake.posts[-1]["sce"] == int(fake.now()) + 86400

        # Three failed polls in a row before it is called offline.
        fake.reachable = False
        await device.async_update()
        await device.async_update()
        assert device.online
        await device.async_update()
        assert not device.online and device.temperature("pit") is None
        fake.reachable = True

        fake.advance(60)
        await device.async_update()
        await device.async_stop()
        assert fake.posts[-1]["acs"] == 0
        assert not device.running


async def test_device_boost_returns_to_target() -> None:
    fake = FakeIkamand()
    p = fake.patches()
    with p[0], p[1], p[2]:
        device = Ikamand(HOST)
        await device.async_update()
        await device.async_set_target(120)
        fake.advance(30)
        await device.async_boost(10)
        assert fake.posts[-1]["tpt"] == 260
        assert device.boosting
        fake.advance(601)
        await device.async_update()
        assert not device.boosting
        assert fake.posts[-1]["tpt"] == 120

        # With no target wanted, the end of a boost turns the fan off.
        await device.async_stop()
        fake.advance(30)
        await device.async_boost(5)
        fake.advance(301)
        await device.async_update()
        assert fake.posts[-1]["acs"] == 0


async def test_device_adopts_running_cook() -> None:
    """Home Assistant restarts while the iKamand is holding a temperature: it is left running."""
    fake = FakeIkamand()
    fake.state.update({"acs": "1", "tpt": "135", "sce": str(int(fake.now()) + 80000)})
    p = fake.patches()
    with p[0], p[1], p[2]:
        device = Ikamand(HOST)
        await device.async_update()
        assert device.wanted_target == 135
        assert fake.posts == []


async def test_cook_with_built_in_ikamand(hass: HomeAssistant) -> None:
    fake = FakeIkamand()
    fake.state["t1"] = "20"
    p = fake.patches()
    with p[0], p[1], p[2], p[3], p[4]:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Cook Manager",
            data={},
            options={"slots": 1, "probes": [], "ikamand_hosts": f"http://{HOST}/", "pre_alert": 5, "grill_tolerance": 15},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        manager = entry.runtime_data
        await manager._async_poll_ikamands()
        await hass.async_block_till_done()

        assert hass.states.get("climate.ikamand").state == "off"
        assert hass.states.get("climate.ikamand").attributes["current_temperature"] == 25
        assert hass.states.get("sensor.ikamand_pit_temperature").state == "25.0"
        assert hass.states.get("sensor.ikamand_probe_1").state == "20.0"
        assert hass.states.get("sensor.ikamand_probe_2").state == "unknown"
        assert hass.states.get("sensor.ikamand_fan").state == "0"
        assert hass.states.get("select.cook_1_probe").attributes["options"] == [
            "None", "iKamand pit", "iKamand probe 1"
        ]
        assert hass.states.get("select.cook_1_grill_controller").attributes["options"] == ["None", "iKamand"]

        # Own entities are accepted in the service and mapped to the built-in ids.
        await hass.services.async_call(
            DOMAIN,
            "start_cook",
            {
                "slot": 1,
                "probe": "sensor.ikamand_probe_1",
                "grill_probe": "sensor.ikamand_pit_temperature",
                "grill_controller": "climate.ikamand",
                "target": 60,
                "grill_target": 150,
            },
            blocking=True,
        )
        slot = manager.slots[0]
        assert (slot.probe, slot.grill_probe, slot.controller) == (PROBE_1, PIT, CONTROLLER)
        assert fake.posts[-1]["acs"] == 1 and fake.posts[-1]["tpt"] == 150
        await hass.async_block_till_done()
        assert hass.states.get("climate.ikamand").state == "heat"
        assert hass.states.get("climate.ikamand").attributes["temperature"] == 150
        assert hass.states.get("sensor.cook_1_temperature").state == "20.0"

        # Changing the grill target during the cook reaches the device.
        await hass.services.async_call(
            "number", "set_value", {"entity_id": "number.cook_1_grill_target", "value": 180}, blocking=True
        )
        assert fake.posts[-1]["tpt"] == 180

        # Meat reaches the target: the cook is over (no recipe, so no rest) and the fan is stopped.
        fake.state.update({"t1": "61", "pt": "180"})
        for _ in range(4):
            fake.advance(15)
            await manager._async_poll_ikamands()
            await manager._async_tick()
        await hass.async_block_till_done()
        assert hass.states.get("sensor.cook_1").state == "done"
        assert fake.posts[-1]["acs"] == 0
        assert hass.states.get("climate.ikamand").state == "off"

        # The thermostat also works by hand.
        await hass.services.async_call(
            "climate", "set_temperature", {"entity_id": "climate.ikamand", "temperature": 200}, blocking=True
        )
        assert fake.posts[-1]["acs"] == 0  # off: only remembered
        await hass.services.async_call("climate", "turn_on", {"entity_id": "climate.ikamand"}, blocking=True)
        assert fake.posts[-1]["acs"] == 1 and fake.posts[-1]["tpt"] == 200
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": "switch.ikamand_fire_it_up"}, blocking=True
        )
        assert fake.posts[-1]["tpt"] == 260
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": "switch.ikamand_fire_it_up"}, blocking=True
        )
        assert fake.posts[-1]["tpt"] == 200
        await hass.services.async_call("climate", "turn_off", {"entity_id": "climate.ikamand"}, blocking=True)
        assert fake.posts[-1]["acs"] == 0
        await hass.services.async_call("button", "press", {"entity_id": "button.cook_1_stop"}, blocking=True)
