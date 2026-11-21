"""Time-remaining estimate from the probe curve.

With a grill reading the meat is modelled as heating towards the grill
temperature (Newton's law): dT/dt = k * (T_grill - T_meat). k is fitted on a
recent window so it follows the cook as it changes. Without a grill reading
the recent slope is extrapolated in a straight line.

Low-and-slow cooks stall (evaporative cooling, roughly 60-80 C). During a
stall no estimate is given; a number would be wrong.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Sample = tuple[float, float | None, float | None]  # (time, meat, grill)

WINDOW_SECONDS = 25 * 60
MIN_SPAN_SECONDS = 5 * 60
MIN_POINTS = 6
MAX_SECONDS = 48 * 3600
MIN_RATE_PER_HOUR = 0.3

STALL_MIN_TARGET = 85.0
STALL_BAND = (55.0, 82.0)
STALL_SPAN_SECONDS = 15 * 60
STALL_RATE_PER_HOUR = 1.5


@dataclass(frozen=True)
class Estimate:
    seconds: float | None
    rate_per_hour: float | None
    stalled: bool = False
    method: str = "none"


def _fit_line(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares slope and the fitted value at the last point."""
    count = len(points)
    mean_x = sum(p[0] for p in points) / count
    mean_y = sum(p[1] for p in points) / count
    var_x = sum((p[0] - mean_x) ** 2 for p in points)
    if var_x == 0:
        return 0.0, mean_y
    slope = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points) / var_x
    return slope, mean_y + slope * (points[-1][0] - mean_x)


def estimate_remaining(samples: list[Sample], target: float, now: float) -> Estimate:
    """Seconds until the meat reaches `target`, or None when it cannot be told."""
    recent = [s for s in samples if s[1] is not None and now - s[0] <= WINDOW_SECONDS]
    if not recent:
        return Estimate(None, None)
    if recent[-1][1] >= target:
        return Estimate(0.0, None, method="reached")
    span = recent[-1][0] - recent[0][0]
    if len(recent) < MIN_POINTS or span < MIN_SPAN_SECONDS:
        return Estimate(None, None, method="gathering")

    slope, current = _fit_line([(s[0], s[1]) for s in recent])
    rate = slope * 3600.0

    stalled = (
        target >= STALL_MIN_TARGET
        and STALL_BAND[0] <= current <= STALL_BAND[1]
        and span >= STALL_SPAN_SECONDS
        and rate < STALL_RATE_PER_HOUR
    )
    if stalled:
        return Estimate(None, rate, stalled=True, method="stalled")
    if rate < MIN_RATE_PER_HOUR:
        return Estimate(None, rate, method="flat")
    if current >= target:
        return Estimate(0.0, rate, method="reached")

    seconds: float | None = None
    method = "linear"
    grill = [s[2] for s in recent if s[2] is not None]
    if len(grill) >= len(recent) * 0.8:
        grill_mean = sum(grill) / len(grill)
        drive = grill_mean - current
        # The slope is the average over the window, so pair it with the average drive.
        mean_drive = grill_mean - sum(s[1] for s in recent) / len(recent)
        if grill_mean > target + 5.0 and drive > 5.0 and mean_drive > 5.0:
            k = slope / mean_drive
            seconds = math.log(drive / (grill_mean - target)) / k
            method = "newton"
    if seconds is None:
        seconds = (target - current) / slope
    if seconds > MAX_SECONDS:
        return Estimate(None, rate, method="too_far")
    return Estimate(max(seconds, 0.0), rate, method=method)
