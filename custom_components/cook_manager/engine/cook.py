"""One cook: stages, alerts and the time-remaining estimate.

The caller feeds readings in with `update(now, meat, grill)` and acts on the
events that come back (notifications, grill setpoint changes). Nothing here
talks to Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from collections.abc import Callable
from typing import Any

from .eta import Estimate, Sample, estimate_remaining
from .recipes import (
    REF_PULL,
    REF_TARGET,
    UNTIL_GRILL,
    UNTIL_MANUAL,
    UNTIL_MEAT,
    UNTIL_MINUTES,
    Stage,
)

SAMPLE_SECONDS = 30
MAX_SAMPLES = 1200
REACH_COUNT = 2
PROBE_LOST_SECONDS = 120
GRILL_ALERT_DELAY = 180
GRILL_ALERT_REPEAT = 900
GRILL_READY_QUIET_SECONDS = 60
ETA_SMOOTHING = 0.3

EVENT_STARTED = "started"
EVENT_STAGE = "stage_started"
EVENT_APPROACHING = "approaching"
EVENT_TARGET = "target_reached"
EVENT_DONE = "done"
EVENT_STOPPED = "stopped"
EVENT_GRILL_READY = "grill_ready"
EVENT_GRILL_LOW = "grill_low"
EVENT_GRILL_HIGH = "grill_high"
EVENT_GRILL_OK = "grill_recovered"
EVENT_PROBE_LOST = "probe_lost"
EVENT_PROBE_BACK = "probe_restored"
EVENT_STALLED = "stalled"


class CookStatus(StrEnum):
    IDLE = "idle"
    COOKING = "cooking"
    RESTING = "resting"
    DONE = "done"


@dataclass
class CookEvent:
    type: str
    message: str
    notify: bool = True
    data: dict[str, Any] = field(default_factory=dict)


def _identity(value: float) -> float:
    return value


class Cook:
    """State of one cook."""

    def __init__(
        self,
        *,
        name: str,
        stages: list[Stage],
        target: float,
        carryover: float = 0.0,
        grill_target: float | None = None,
        recipe_id: str | None = None,
        recipe_name: str | None = None,
        doneness: str | None = None,
        expects_meat: bool = True,
        pre_alert: float = 5.0,
        grill_tolerance: float = 15.0,
        display_temp: Callable[[float], float] = _identity,
        display_delta: Callable[[float], float] = _identity,
    ) -> None:
        # display_temp / display_delta convert Celsius to the unit used in messages.
        self._display_temp = display_temp
        self._display_delta = display_delta
        self.name = name
        self.stages = stages
        self.target = target
        self.carryover = carryover
        self.grill_target = grill_target
        self.recipe_id = recipe_id
        self.recipe_name = recipe_name
        self.doneness = doneness
        self.expects_meat = expects_meat
        self.pre_alert = pre_alert
        self.grill_tolerance = grill_tolerance

        self.status = CookStatus.IDLE
        self.stage_index = 0
        self.started_at: float | None = None
        self.stage_started_at: float | None = None
        self.finished_at: float | None = None

        self.meat: float | None = None
        self.grill: float | None = None
        self.start_meat: float | None = None
        self.max_meat: float | None = None
        self.meat_seen_at: float | None = None
        self.samples: list[Sample] = []

        self.probe_lost = False
        self.stalled = False
        self.estimate = Estimate(None, None)
        self.eta_seconds: float | None = None
        self.eta_at: float | None = None

        self._approaching_sent = False
        self._reach_count = 0
        self._grill_ready = False
        self._grill_band_since: float | None = None
        self._grill_out_since: float | None = None
        self._grill_last_alert: float | None = None
        self._grill_alerting: str | None = None

    def _t(self, value: float) -> str:
        return f"{self._display_temp(value):.0f}"

    def _d(self, value: float) -> str:
        return f"{self._display_delta(value):.0f}"

    # ----- derived values -------------------------------------------------

    @property
    def pull_temp(self) -> float:
        return self.target - self.carryover

    @property
    def stage(self) -> Stage | None:
        if self.status in (CookStatus.IDLE, CookStatus.DONE):
            return None
        return self.stages[self.stage_index]

    @property
    def active(self) -> bool:
        return self.status in (CookStatus.COOKING, CookStatus.RESTING)

    @property
    def grill_state(self) -> str | None:
        """ok / low / high once the grill has first reached its band."""
        if self.grill_target is None or self.grill is None or not self._grill_ready:
            return None
        if self.grill < self.grill_target - self.grill_tolerance:
            return "low"
        if self.grill > self.grill_target + self.grill_tolerance:
            return "high"
        return "ok"

    def stage_threshold(self, stage: Stage) -> float | None:
        if stage.until_kind == UNTIL_MEAT:
            if stage.until_value == REF_PULL:
                return self.pull_temp
            if stage.until_value == REF_TARGET:
                return self.target
            return float(stage.until_value)  # type: ignore[arg-type]
        if stage.until_kind == UNTIL_GRILL:
            return float(stage.until_value)  # type: ignore[arg-type]
        return None

    def remaining_seconds(self, now: float) -> float | None:
        """Time to the pull temperature while cooking, rest time left while resting."""
        stage = self.stage
        if stage is None:
            return None
        if self.status == CookStatus.RESTING:
            if stage.until_kind == UNTIL_MINUTES and self.stage_started_at is not None:
                return max(float(stage.until_value) * 60 - (now - self.stage_started_at), 0.0)
            return None
        if self.eta_seconds is None or self.eta_at is None:
            return None
        return max(self.eta_seconds - (now - self.eta_at), 0.0)

    def progress(self) -> float | None:
        if self.status == CookStatus.DONE:
            return 100.0
        if self.status == CookStatus.RESTING:
            return 100.0
        if self.meat is None or self.start_meat is None:
            return None
        span = self.pull_temp - self.start_meat
        if span <= 0:
            return 100.0
        return round(min(max((self.meat - self.start_meat) / span * 100.0, 0.0), 100.0), 1)

    # ----- commands -------------------------------------------------------

    def start(self, now: float) -> list[CookEvent]:
        self.status = CookStatus.COOKING
        self.started_at = now
        self.finished_at = None
        self.samples = []
        self.start_meat = None
        self.max_meat = None
        self.meat_seen_at = None
        self.probe_lost = False
        self.stalled = False
        self.estimate = Estimate(None, None)
        self.eta_seconds = None
        self.eta_at = None
        events = [
            CookEvent(
                EVENT_STARTED,
                f"{self.name}: cook started",
                notify=False,
                data={"target": self.target, "pull_temp": self.pull_temp},
            )
        ]
        events.extend(self._enter_stage(0, now))
        return events

    def stop(self, now: float) -> list[CookEvent]:
        if self.status == CookStatus.IDLE:
            return []
        was_active = self.active
        if self.finished_at is None:
            self.finished_at = now
        self.status = CookStatus.IDLE
        return [CookEvent(EVENT_STOPPED, f"{self.name}: cook stopped", notify=False, data={"was_active": was_active})]

    def next_stage(self, now: float) -> list[CookEvent]:
        if not self.active:
            return []
        return self._advance(now)

    def set_target(self, target: float) -> None:
        self.target = target
        self._approaching_sent = False
        self._reach_count = 0

    def set_grill_target(self, grill_target: float | None, now: float) -> None:
        if grill_target == self.grill_target:
            return
        self.grill_target = grill_target
        self._reset_grill_band(now)

    # ----- the tick -------------------------------------------------------

    def update(self, now: float, meat: float | None, grill: float | None) -> list[CookEvent]:
        """Feed the latest readings; returns what happened."""
        self.grill = grill
        if meat is not None:
            self.meat = meat
        if not self.active:
            return []

        events: list[CookEvent] = []
        events.extend(self._track_probe(now, meat))

        sampled = False
        if not self.samples or now - self.samples[-1][0] >= SAMPLE_SECONDS:
            self.samples.append((now, meat, grill))
            self._trim_samples()
            sampled = True

        stage = self.stages[self.stage_index]
        if self._stage_complete(stage, now, meat, grill, events):
            events.extend(self._advance(now))
            return events

        if self.status == CookStatus.COOKING:
            events.extend(self._check_grill(now, grill))
            if sampled:
                events.extend(self._update_estimate(now))
        return events

    # ----- internals ------------------------------------------------------

    def _track_probe(self, now: float, meat: float | None) -> list[CookEvent]:
        if not self.expects_meat:
            return []
        if meat is not None:
            self.meat_seen_at = now
            if self.start_meat is None:
                self.start_meat = meat
            if self.max_meat is None or meat > self.max_meat:
                self.max_meat = meat
            if self.probe_lost:
                self.probe_lost = False
                return [CookEvent(EVENT_PROBE_BACK, f"{self.name}: probe reading again")]
            return []
        reference = self.meat_seen_at if self.meat_seen_at is not None else self.started_at
        if not self.probe_lost and reference is not None and now - reference >= PROBE_LOST_SECONDS:
            self.probe_lost = True
            return [CookEvent(EVENT_PROBE_LOST, f"{self.name}: no reading from the meat probe")]
        return []

    def _stage_complete(
        self,
        stage: Stage,
        now: float,
        meat: float | None,
        grill: float | None,
        events: list[CookEvent],
    ) -> bool:
        if stage.until_kind == UNTIL_MANUAL:
            return False
        if stage.until_kind == UNTIL_MINUTES:
            return self.stage_started_at is not None and now - self.stage_started_at >= float(stage.until_value) * 60
        threshold = self.stage_threshold(stage)
        reading = meat if stage.until_kind == UNTIL_MEAT else grill
        if threshold is None or reading is None:
            return False
        if reading >= threshold:
            self._reach_count += 1
        else:
            self._reach_count = 0
            if (
                stage.until_kind == UNTIL_MEAT
                and not self._approaching_sent
                and reading >= threshold - self.pre_alert
            ):
                self._approaching_sent = True
                events.append(
                    CookEvent(
                        EVENT_APPROACHING,
                        f"{self.name}: {self._t(reading)}°, {self._d(threshold - reading)}° to go",
                        data={"temperature": reading, "threshold": threshold, "stage": stage.id},
                    )
                )
        if self._reach_count < REACH_COUNT:
            return False
        if stage.until_kind == UNTIL_MEAT and stage.until_value in (REF_PULL, REF_TARGET):
            events.append(
                CookEvent(
                    EVENT_TARGET,
                    f"{self.name}: reached {self._t(reading)}°, take it off",
                    data={"temperature": reading, "threshold": threshold},
                )
            )
        return True

    def _enter_stage(self, index: int, now: float) -> list[CookEvent]:
        stage = self.stages[index]
        self.stage_index = index
        self.stage_started_at = now
        self._approaching_sent = False
        self._reach_count = 0
        self.status = CookStatus.RESTING if stage.rest else CookStatus.COOKING
        if stage.grill_temp is not None and stage.grill_temp != self.grill_target:
            self.grill_target = stage.grill_temp
            self._reset_grill_band(now)
        elif index == 0:
            self._reset_grill_band(now)
        if stage.rest:
            self.eta_seconds = None
            self.stalled = False
        message = stage.notify or f"{self.name}: {stage.name}"
        if stage.notify:
            message = f"{self.name}: {stage.notify}"
        return [
            CookEvent(
                EVENT_STAGE,
                message,
                notify=bool(stage.notify) or index > 0,
                data={
                    "stage": stage.id,
                    "stage_name": stage.name,
                    "stage_index": index,
                    "grill_temp": stage.grill_temp,
                    "rest": stage.rest,
                },
            )
        ]

    def _advance(self, now: float) -> list[CookEvent]:
        if self.stage_index + 1 < len(self.stages):
            return self._enter_stage(self.stage_index + 1, now)
        self.status = CookStatus.DONE
        self.finished_at = now
        self.eta_seconds = None
        self.stalled = False
        return [CookEvent(EVENT_DONE, f"{self.name}: done", data={"max_meat": self.max_meat})]

    def _reset_grill_band(self, now: float) -> None:
        self._grill_ready = False
        self._grill_band_since = now
        self._grill_out_since = None
        self._grill_last_alert = None
        self._grill_alerting = None

    def _check_grill(self, now: float, grill: float | None) -> list[CookEvent]:
        if self.grill_target is None or grill is None:
            return []
        low = self.grill_target - self.grill_tolerance
        high = self.grill_target + self.grill_tolerance
        if not self._grill_ready:
            if grill < low:
                return []
            self._grill_ready = True
            waited = now - (self._grill_band_since if self._grill_band_since is not None else now)
            return [
                CookEvent(
                    EVENT_GRILL_READY,
                    f"{self.name}: grill at {self._t(grill)}°",
                    notify=waited >= GRILL_READY_QUIET_SECONDS,
                    data={"temperature": grill, "grill_target": self.grill_target},
                )
            ]
        state = "low" if grill < low else "high" if grill > high else None
        if state is None:
            self._grill_out_since = None
            if self._grill_alerting is None:
                return []
            self._grill_alerting = None
            self._grill_last_alert = None
            return [
                CookEvent(
                    EVENT_GRILL_OK,
                    f"{self.name}: grill back at {self._t(grill)}°",
                    data={"temperature": grill, "grill_target": self.grill_target},
                )
            ]
        if self._grill_out_since is None:
            self._grill_out_since = now
        if now - self._grill_out_since < GRILL_ALERT_DELAY:
            return []
        if self._grill_last_alert is not None and now - self._grill_last_alert < GRILL_ALERT_REPEAT:
            return []
        self._grill_last_alert = now
        self._grill_alerting = state
        return [
            CookEvent(
                EVENT_GRILL_LOW if state == "low" else EVENT_GRILL_HIGH,
                f"{self.name}: grill {state} at {self._t(grill)}° (target {self._t(self.grill_target)}°)",
                data={"temperature": grill, "grill_target": self.grill_target},
            )
        ]

    def _update_estimate(self, now: float) -> list[CookEvent]:
        if not self.expects_meat:
            return []
        estimate = estimate_remaining(self.samples, self.pull_temp, now)
        self.estimate = estimate
        events: list[CookEvent] = []
        if estimate.stalled and not self.stalled:
            events.append(
                CookEvent(
                    EVENT_STALLED,
                    f"{self.name}: stalled at {self._t(self.meat or 0)}°",
                    data={"temperature": self.meat},
                )
            )
        self.stalled = estimate.stalled
        if estimate.seconds is None:
            self.eta_seconds = None
        elif self.eta_seconds is None or self.eta_at is None:
            self.eta_seconds = estimate.seconds
        else:
            previous = max(self.eta_seconds - (now - self.eta_at), 0.0)
            self.eta_seconds = (1 - ETA_SMOOTHING) * previous + ETA_SMOOTHING * estimate.seconds
        self.eta_at = now
        return events

    def _trim_samples(self) -> None:
        if len(self.samples) <= MAX_SAMPLES:
            return
        half = len(self.samples) // 2
        self.samples = self.samples[:half:2] + self.samples[half:]

    # ----- persistence ----------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Record of a finished cook, for the history."""
        step = max(len(self.samples) // 240, 1)
        return {
            "name": self.name,
            "recipe_id": self.recipe_id,
            "recipe_name": self.recipe_name,
            "doneness": self.doneness,
            "target": self.target,
            "carryover": self.carryover,
            "grill_target": self.grill_target,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "start_meat": self.start_meat,
            "max_meat": self.max_meat,
            "completed": self.status == CookStatus.DONE,
            "curve": [[round(s[0]), s[1], s[2]] for s in self.samples[::step]],
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stages": [stage.as_dict() for stage in self.stages],
            "target": self.target,
            "carryover": self.carryover,
            "grill_target": self.grill_target,
            "recipe_id": self.recipe_id,
            "recipe_name": self.recipe_name,
            "doneness": self.doneness,
            "expects_meat": self.expects_meat,
            "pre_alert": self.pre_alert,
            "grill_tolerance": self.grill_tolerance,
            "status": self.status.value,
            "stage_index": self.stage_index,
            "started_at": self.started_at,
            "stage_started_at": self.stage_started_at,
            "finished_at": self.finished_at,
            "meat": self.meat,
            "start_meat": self.start_meat,
            "max_meat": self.max_meat,
            "meat_seen_at": self.meat_seen_at,
            "samples": [list(sample) for sample in self.samples],
            "grill_ready": self._grill_ready,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        display_temp: Callable[[float], float] = _identity,
        display_delta: Callable[[float], float] = _identity,
    ) -> Cook:
        cook = cls(
            display_temp=display_temp,
            display_delta=display_delta,
            name=data["name"],
            stages=[Stage.from_dict(item) for item in data["stages"]],
            target=data["target"],
            carryover=data.get("carryover", 0.0),
            grill_target=data.get("grill_target"),
            recipe_id=data.get("recipe_id"),
            recipe_name=data.get("recipe_name"),
            doneness=data.get("doneness"),
            expects_meat=data.get("expects_meat", True),
            pre_alert=data.get("pre_alert", 5.0),
            grill_tolerance=data.get("grill_tolerance", 15.0),
        )
        cook.status = CookStatus(data.get("status", "idle"))
        cook.stage_index = min(data.get("stage_index", 0), max(len(cook.stages) - 1, 0))
        cook.started_at = data.get("started_at")
        cook.stage_started_at = data.get("stage_started_at")
        cook.finished_at = data.get("finished_at")
        cook.meat = data.get("meat")
        cook.start_meat = data.get("start_meat")
        cook.max_meat = data.get("max_meat")
        cook.meat_seen_at = data.get("meat_seen_at")
        cook.samples = [(s[0], s[1], s[2]) for s in data.get("samples", [])]
        cook._grill_ready = bool(data.get("grill_ready", False))
        return cook
