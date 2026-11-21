"""Direct local connection to a Kamado Joe iKamand (first generation).

The iKamand serves three plain HTTP endpoints on the local network:

    GET  /cgi-bin/info   firmware version and MAC address
    GET  /cgi-bin/data   temperatures, fan output, cook state
    POST /cgi-bin/cook   start, retarget or stop a cook

Replies and commands are URL-encoded key=value pairs. Temperatures are whole degrees
Celsius. The iKamand does its own fan regulation; this module only tells it the pit target
and makes sure it keeps that target (after a power blip, a dropped connection or the
24 hour limit the device puts on a cook).

The iKamand's web server does not send valid HTTP (two status lines, no headers), which
strict clients such as aiohttp refuse. So the requests are made over a plain socket and the
reply is read leniently.

Protocol knowledge comes from the plmilord/Hass.io-custom-component-ikamand project.
No Home Assistant imports here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import parse_qs, urlencode

_LOGGER = logging.getLogger(__name__)

MIN_TARGET = 66
MAX_TARGET = 260
BOOST_TARGET = 260
DEFAULT_BOOST_MINUTES = 10

_NO_PROBE = ("-400", "400")
_TIMEOUT = 8
_OFFLINE_AFTER = 3  # failed polls in a row
_BOOT_SECONDS = 40  # the device clock restarts near zero after power-up
_CLOCK_DRIFT = 60
_COOK_SECONDS = 86400  # the device wants an end time for every cook
_RENEW_BEFORE = 3600
_REPOST_GAP = 12  # give the device time to show a command before repeating it

_MAX_REPLY = 16384


def parse_reply(raw: bytes) -> dict[str, str] | None:
    """Status and key=value body out of the iKamand's reply; None unless it says 200.

    A reply looks like b"HTTP/1.0 200 OK\r\nHTTP/1.1 200 OK\r\n\ntime=1&acs=0&pt=28".
    """
    lines = [line.strip() for line in raw.decode("ascii", errors="replace").splitlines()]
    lines = [line for line in lines if line]
    if not lines or not lines[0].startswith("HTTP/") or " 200" not in lines[0]:
        return None
    body = next((line for line in reversed(lines) if not line.startswith("HTTP/") and "=" in line), "")
    return {key: values[0] for key, values in parse_qs(body, keep_blank_values=True).items()}

CHANNELS = {"pit": "pt", "p1": "t1", "p2": "t2", "p3": "t3"}


class Ikamand:
    """One iKamand: last reading, the wanted state, and keeping the device on it."""

    def __init__(self, host: str, port: int = 80) -> None:
        self.host = host
        self._port = port
        self.data: dict[str, str] = {}
        self.info: dict[str, str] = {}
        self.online = False
        self.wanted_target: int | None = None  # None means off
        self.last_target = 110  # what "on" means when no target is given
        self.boost_until: float | None = None
        self.boost_minutes = DEFAULT_BOOST_MINUTES
        self._adopted = False
        self._fails = 0
        self._last_post = 0.0
        self._cook_end = 0.0  # end time last sent; the device does not always report it

    # ----- transport ------------------------------------------------------

    async def _request(self, method: str, path: str, body: str = "") -> dict[str, str] | None:
        """One request on its own connection. None when the device did not answer with 200."""
        head = [
            f"{method} /cgi-bin/{path} HTTP/1.1",
            f"Host: {self.host}",
            "User-Agent: ikamand",
            "Accept: application/json",
            "Connection: close",
        ]
        if method == "POST":
            # The vendor app sent a form-encoded body under a JSON content type.
            head += ["Content-Type: application/json", f"Content-Length: {len(body)}"]
        request = ("\r\n".join(head) + "\r\n\r\n" + body).encode("ascii")
        writer = None
        try:
            async with asyncio.timeout(_TIMEOUT):
                reader, writer = await asyncio.open_connection(self.host, self._port)
                writer.write(request)
                await writer.drain()
                raw = await reader.read(_MAX_REPLY)
                while len(raw) < _MAX_REPLY and (more := await reader.read(_MAX_REPLY)):
                    raw += more
        except (TimeoutError, OSError) as err:
            _LOGGER.debug("iKamand %s: %s %s failed: %r", self.host, method, path, err)
            return None
        finally:
            if writer is not None:
                writer.close()
        reply = parse_reply(raw)
        if reply is None:
            _LOGGER.debug("iKamand %s: %s %s answered %r", self.host, method, path, raw[:200])
        return reply

    async def _get(self, path: str) -> dict[str, str] | None:
        return await self._request("GET", path)

    async def _post(self, payload: dict[str, Any]) -> bool:
        self._last_post = time.time()
        return await self._request("POST", "cook", urlencode(payload)) is not None

    # ----- readings -------------------------------------------------------

    def _int(self, key: str) -> int:
        try:
            return int(float(self.data.get(key, "0")))
        except ValueError:
            return 0

    def temperature(self, channel: str) -> float | None:
        """Pit ('pit') or food probe ('p1'..'p3') in Celsius; None with no probe or offline."""
        if not self.online:
            return None
        raw = self.data.get(CHANNELS[channel])
        if raw is None or raw in _NO_PROBE:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    @property
    def fan(self) -> int | None:
        return self._int("dc") if self.online else None

    @property
    def running(self) -> bool:
        """The device reports a cook in progress."""
        return self.online and self.data.get("acs") == "1"

    @property
    def target(self) -> int | None:
        """Pit target the device is holding, None when it is off."""
        return self._int("tpt") if self.running else None

    @property
    def boosting(self) -> bool:
        return self.boost_until is not None

    @property
    def firmware(self) -> str | None:
        return self.info.get("fw_version")

    @property
    def mac(self) -> str | None:
        return self.info.get("MAC")

    # ----- commands -------------------------------------------------------

    def want(self, celsius: float) -> None:
        """Set the pit temperature to hold without sending it; the next poll sends it."""
        self.wanted_target = self.last_target = max(MIN_TARGET, min(MAX_TARGET, round(celsius)))
        self._adopted = True

    async def async_set_target(self, celsius: float) -> None:
        """Hold this pit temperature. Starts the iKamand if it is off."""
        self.want(celsius)
        if not self.boosting:
            await self._send_wanted(time.time())

    async def async_stop(self) -> None:
        """Fan off, cook ended. Also ends a boost."""
        self.wanted_target = None
        self.boost_until = None
        self._adopted = True
        await self._send_wanted(time.time())

    async def async_boost(self, minutes: float | None = None) -> None:
        """Fan flat out to light the fire, then back to the wanted target (or off)."""
        now = time.time()
        self.boost_until = now + 60 * (minutes if minutes is not None else self.boost_minutes)
        self._adopted = True
        await self._send_wanted(now)

    async def async_end_boost(self) -> None:
        if self.boosting:
            self.boost_until = None
            await self._send_wanted(time.time())

    def _wanted(self, now: float) -> tuple[int | None, int]:
        """(pit target or None for off, cook end time) the device should be on."""
        if self.boost_until is not None:
            return BOOST_TARGET, int(self.boost_until)
        if self.wanted_target is not None:
            return self.wanted_target, int(now) + _COOK_SECONDS
        return None, 0

    async def _send_wanted(self, now: float) -> bool:
        target, end = self._wanted(now)
        payload = {
            "acs": 1 if target is not None else 0,
            "csid": "",
            "tpt": target or 0,
            "sce": end,
            "p": 0,
            "tft": 0,
            "as": 0,
            "ct": int(now),
        }
        ok = await self._post(payload)
        if ok and self.data:
            # Show the change at once; the next poll confirms it.
            self.data["acs"] = str(payload["acs"])
            self.data["tpt"] = str(payload["tpt"])
            self.data["sce"] = str(end)
        if ok:
            self._cook_end = float(end)
        return ok

    # ----- polling --------------------------------------------------------

    async def async_update(self) -> None:
        """Read the device and put it back on the wanted state if it has drifted off it."""
        now = time.time()
        if self.boost_until is not None and now >= self.boost_until:
            self.boost_until = None

        data = await self._get("data")
        if data is None or "time" not in data:
            self._fails += 1
            if self._fails >= _OFFLINE_AFTER:
                self.online = False
            return
        self._fails = 0
        try:
            device_clock = int(float(data["time"]))
        except ValueError:
            device_clock = 0
        self.data = data
        if not self.online:
            self.online = True
            if not self.info:
                self.info = await self._get("info") or {}

        if not self._adopted:
            # First contact since Home Assistant started: carry on with what the device is doing.
            # A device that has just powered up has nothing to carry on with.
            self._adopted = True
            if device_clock >= _BOOT_SECONDS and data.get("acs") == "1":
                self.wanted_target = self._int("tpt") or None
                self._cook_end = float(self._int("sce"))
                self.last_target = self.wanted_target or self.last_target

        if now - self._last_post < _REPOST_GAP:
            return
        target, _end = self._wanted(now)
        if target is None:
            drifted = data.get("acs") == "1"
        else:
            drifted = (
                data.get("acs") != "1"
                or self._int("tpt") != target
                or (not self.boosting and self._cook_end - now < _RENEW_BEFORE)
            )
        clock_off = target is not None and abs(device_clock - now) > _CLOCK_DRIFT
        if drifted or clock_off:
            _LOGGER.debug("iKamand %s is off its wanted state, sending it again", self.host)
            await self._send_wanted(now)
