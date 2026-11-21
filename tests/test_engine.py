"""Cook engine tests."""

import math
from pathlib import Path

import pytest
import yaml

from custom_components.cook_manager.engine import (
    Cook,
    CookStatus,
    RecipeError,
    build_stages,
    estimate_remaining,
    parse_recipe,
)
from custom_components.cook_manager.engine import cook as cook_mod

BUILTIN = Path(__file__).parent.parent / "custom_components" / "cook_manager" / "recipes" / "builtin.yaml"


def load_builtin():
    raw = yaml.safe_load(BUILTIN.read_text(encoding="utf-8"))
    return {recipe.id: recipe for recipe in (parse_recipe(item) for item in raw)}


def run(cook, start, seconds, meat_fn, grill_fn=lambda t: None, step=15, until=None):
    """Feed readings every `step` seconds; return (events, end time)."""
    events = []
    t = start
    while t <= start + seconds and cook.active and cook.status != until:
        events.extend(cook.update(t, meat_fn(t - start), grill_fn(t - start)))
        t += step
    return events, t


def types(events):
    return [event.type for event in events]


def test_builtin_recipes_are_valid():
    recipes = load_builtin()
    assert len(recipes) >= 15
    for recipe in recipes.values():
        assert recipe.default_target() is not None
        if recipe.safe_min is not None and not recipe.doneness:
            assert recipe.default_target() >= recipe.safe_min, recipe.id
        assert build_stages(recipe)


def test_recipe_errors():
    with pytest.raises(RecipeError):
        parse_recipe({"id": "x"})
    with pytest.raises(RecipeError):
        parse_recipe({"id": "x", "target": 60, "stages": [{"until": {"bogus": 1}}]})
    with pytest.raises(RecipeError):
        parse_recipe({"id": "x", "target": 60, "stages": [{"until": {"minutes": 0}}]})


def test_estimate_newton_is_close():
    grill, k, start_temp, target = 120.0, 1 / 9000, 10.0, 60.0

    def meat(t):
        return grill - (grill - start_temp) * math.exp(-k * t)

    true_total = math.log((grill - start_temp) / (grill - target)) / k
    now = 3600.0
    samples = [(float(t), meat(t), grill) for t in range(0, 3601, 30)]
    estimate = estimate_remaining(samples, target, now)
    assert estimate.method == "newton"
    assert estimate.seconds == pytest.approx(true_total - now, rel=0.05)


def test_estimate_linear_without_grill():
    samples = [(float(t), 20 + t / 60.0, None) for t in range(0, 1201, 30)]
    estimate = estimate_remaining(samples, 60.0, 1200.0)
    assert estimate.method == "linear"
    assert estimate.seconds == pytest.approx(20 * 60, rel=0.02)
    assert estimate.rate_per_hour == pytest.approx(60, rel=0.02)


def test_estimate_needs_data_and_detects_stall():
    assert estimate_remaining([(0.0, 20.0, None)], 60.0, 0.0).seconds is None
    flat = [(float(t), 68.0 + t / 36000.0, 120.0) for t in range(0, 1501, 30)]
    estimate = estimate_remaining(flat, 93.0, 1500.0)
    assert estimate.stalled
    assert estimate.seconds is None


def test_simple_cook_runs_to_done():
    recipe = load_builtin()["beef_steak"]
    cook = Cook(
        name="Steak",
        stages=build_stages(recipe),
        target=57,
        carryover=recipe.carryover,
        grill_target=230,
    )
    events = cook.start(0)
    assert cook.status == CookStatus.COOKING
    assert cook.pull_temp == 54
    more, t = run(cook, 0, 3600, lambda s: 10 + s / 20.0, lambda s: 230.0, until=CookStatus.RESTING)
    events += more
    assert cook_mod.EVENT_APPROACHING in types(events)
    assert cook_mod.EVENT_TARGET in types(events)
    assert cook.status == CookStatus.RESTING
    assert cook.remaining_seconds(t) == pytest.approx(300, abs=30)
    more, _ = run(cook, t, 400, lambda s: 56.0)
    assert types(more)[-1] == cook_mod.EVENT_DONE
    assert cook.status == CookStatus.DONE
    assert cook.progress() == 100.0


def test_time_remaining_appears_during_a_cook():
    cook = Cook(name="Roast", stages=build_stages(None), target=70, grill_target=160)
    cook.start(0)
    _, t = run(cook, 0, 1200, lambda s: 10 + s / 60.0, lambda s: 160.0)
    remaining = cook.remaining_seconds(t)
    assert remaining is not None
    assert 20 * 60 < remaining < 60 * 60


def test_staged_cook_changes_grill_target():
    recipe = load_builtin()["beef_brisket"]
    cook = Cook(name="Brisket", stages=build_stages(recipe), target=93)
    events = cook.start(0)
    assert events[-1].data["grill_temp"] == 120
    assert cook.grill_target == 120
    more, t = run(cook, 0, 600, lambda s: 71.0, lambda s: 120.0)
    stage_events = [event for event in more if event.type == cook_mod.EVENT_STAGE]
    assert stage_events[0].data["grill_temp"] == 135
    assert cook.grill_target == 135
    assert cook.stage.id == "wrapped"
    more, t = run(cook, t, 600, lambda s: 94.0, lambda s: 135.0)
    assert cook_mod.EVENT_TARGET in types(more)
    assert cook.status == CookStatus.RESTING


def test_stage_notify_message_is_sent_on_entering_the_stage():
    recipe = parse_recipe(
        {
            "id": "two",
            "target": 60,
            "stages": [
                {"id": "a", "until": {"meat_temp": 40}},
                {"id": "b", "until": {"meat_temp": "pull"}, "notify": "Turn it over"},
            ],
        }
    )
    cook = Cook(name="Two", stages=build_stages(recipe), target=60)
    cook.start(0)
    events, _ = run(cook, 0, 60, lambda s: 41.0)
    stage_event = next(event for event in events if event.type == cook_mod.EVENT_STAGE)
    assert stage_event.message == "Two: Turn it over"
    assert stage_event.notify


def test_grill_alerts_and_recovery():
    cook = Cook(name="Roast", stages=build_stages(None), target=90, grill_target=150, grill_tolerance=15)
    cook.start(0)
    events, t = run(cook, 0, 300, lambda s: 20.0, lambda s: 150.0)
    assert cook_mod.EVENT_GRILL_READY in types(events)
    events, t = run(cook, t, 600, lambda s: 21.0, lambda s: 100.0)
    assert types(events).count(cook_mod.EVENT_GRILL_LOW) == 1
    assert cook.grill_state == "low"
    events, t = run(cook, t, 60, lambda s: 22.0, lambda s: 150.0)
    assert cook_mod.EVENT_GRILL_OK in types(events)


def test_probe_lost_and_restored():
    cook = Cook(name="Roast", stages=build_stages(None), target=90)
    cook.start(0)
    run(cook, 0, 60, lambda s: 20.0)
    events, t = run(cook, 75, 300, lambda s: None)
    assert types(events).count(cook_mod.EVENT_PROBE_LOST) == 1
    events, _ = run(cook, t, 30, lambda s: 21.0)
    assert cook_mod.EVENT_PROBE_BACK in types(events)


def test_manual_stage_and_stop():
    recipe = parse_recipe(
        {
            "id": "m",
            "target": 60,
            "stages": [
                {"id": "a", "until": {"manual": True}},
                {"id": "b", "until": {"meat_temp": "pull"}},
            ],
        }
    )
    cook = Cook(name="M", stages=build_stages(recipe), target=60)
    cook.start(0)
    run(cook, 0, 600, lambda s: 99.0)
    assert cook.stage.id == "a"
    cook.next_stage(700)
    assert cook.stage.id == "b"
    assert types(cook.stop(800)) == [cook_mod.EVENT_STOPPED]
    assert cook.status == CookStatus.IDLE


def test_round_trip():
    cook = Cook(name="Roast", stages=build_stages(None, rest_minutes=10), target=70, grill_target=160)
    cook.start(0)
    run(cook, 0, 900, lambda s: 20 + s / 60.0, lambda s: 160.0)
    restored = Cook.from_dict(cook.as_dict())
    assert restored.status == cook.status
    assert restored.samples == cook.samples
    assert restored.stage.id == cook.stage.id
    assert restored.summary()["curve"]


def test_messages_use_display_unit():
    cook = Cook(
        name="Roast",
        stages=build_stages(None),
        target=60,
        display_temp=lambda c: c * 9 / 5 + 32,
        display_delta=lambda c: c * 9 / 5,
    )
    cook.start(0)
    events, _ = run(cook, 0, 60, lambda s: 57.0)
    approaching = next(event for event in events if event.type == cook_mod.EVENT_APPROACHING)
    assert "135°" in approaching.message
    assert "5° to go" in approaching.message
