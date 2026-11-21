"""Recipe model and parsing.

A recipe is a plain mapping (loaded from YAML by the caller):

    id: beef_brisket
    name: Beef brisket
    category: beef
    grill_temp: 120          # default grill target, optional
    carryover: 0             # pull this many degrees before the target
    rest_minutes: 60
    safe_min: 63             # food-safety minimum, informational
    doneness:                # optional; first entry is the default
      - {id: sliced, name: Sliced, target: 93}
    target: 93               # used when there is no doneness list
    stages:                  # optional; default is "cook to pull temp, then rest"
      - id: smoke
        name: Smoke
        grill_temp: 120
        until: {meat_temp: 70}
        notify: Wrap the brisket
      - id: wrapped
        name: Wrapped
        grill_temp: 135
        until: {meat_temp: pull}
      - id: rest
        name: Rest
        rest: true
        until: {minutes: 60}

`until` takes exactly one of:
    meat_temp: <number> | target | pull
    grill_temp: <number>      (grill reaches this, e.g. a preheat stage)
    minutes: <number>         (time in the stage)
    manual: true              (wait for "next stage")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

UNTIL_MEAT = "meat_temp"
UNTIL_GRILL = "grill_temp"
UNTIL_MINUTES = "minutes"
UNTIL_MANUAL = "manual"
UNTIL_KINDS = (UNTIL_MEAT, UNTIL_GRILL, UNTIL_MINUTES, UNTIL_MANUAL)

REF_TARGET = "target"
REF_PULL = "pull"


class RecipeError(ValueError):
    """A recipe mapping is not valid."""


@dataclass(frozen=True)
class Doneness:
    id: str
    name: str
    target: float


@dataclass(frozen=True)
class Stage:
    id: str
    name: str
    until_kind: str
    until_value: float | str | None = None
    grill_temp: float | None = None
    notify: str | None = None
    rest: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "until_kind": self.until_kind,
            "until_value": self.until_value,
            "grill_temp": self.grill_temp,
            "notify": self.notify,
            "rest": self.rest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Stage:
        return cls(
            id=data["id"],
            name=data["name"],
            until_kind=data["until_kind"],
            until_value=data.get("until_value"),
            grill_temp=data.get("grill_temp"),
            notify=data.get("notify"),
            rest=bool(data.get("rest", False)),
        )


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    category: str = "other"
    grill_temp: float | None = None
    carryover: float = 0.0
    rest_minutes: float = 0.0
    safe_min: float | None = None
    target: float | None = None
    doneness: tuple[Doneness, ...] = ()
    stages: tuple[Stage, ...] = ()
    notes: str | None = None
    source: str = "built-in"
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    def default_target(self) -> float | None:
        if self.doneness:
            return self.doneness[0].target
        return self.target

    def target_for(self, doneness_id: str | None) -> float | None:
        for item in self.doneness:
            if item.id == doneness_id:
                return item.target
        return self.default_target()


def _number(value: Any, what: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecipeError(f"{what} must be a number, got {value!r}")
    return float(value)


def _parse_stage(raw: Any, index: int) -> Stage:
    if not isinstance(raw, dict):
        raise RecipeError(f"stage {index + 1} must be a mapping")
    stage_id = str(raw.get("id") or f"stage_{index + 1}")
    name = str(raw.get("name") or stage_id.replace("_", " ").capitalize())
    until = raw.get("until")
    if not isinstance(until, dict) or len(until) != 1:
        raise RecipeError(f"stage '{stage_id}': 'until' needs exactly one of {UNTIL_KINDS}")
    kind, value = next(iter(until.items()))
    if kind not in UNTIL_KINDS:
        raise RecipeError(f"stage '{stage_id}': unknown until '{kind}'")
    if kind == UNTIL_MANUAL:
        until_value: float | str | None = None
    elif kind == UNTIL_MEAT and value in (REF_TARGET, REF_PULL):
        until_value = value
    else:
        until_value = _number(value, f"stage '{stage_id}' until {kind}")
        if kind == UNTIL_MINUTES and until_value <= 0:
            raise RecipeError(f"stage '{stage_id}': minutes must be above zero")
    return Stage(
        id=stage_id,
        name=name,
        until_kind=kind,
        until_value=until_value,
        grill_temp=_number(raw.get("grill_temp"), f"stage '{stage_id}' grill_temp", allow_none=True),
        notify=str(raw["notify"]) if raw.get("notify") else None,
        rest=bool(raw.get("rest", False)),
    )


def parse_recipe(raw: Any, *, source: str = "built-in") -> Recipe:
    """Validate a recipe mapping and return a Recipe."""
    if not isinstance(raw, dict):
        raise RecipeError("recipe must be a mapping")
    recipe_id = raw.get("id")
    if not recipe_id or not isinstance(recipe_id, str):
        raise RecipeError("recipe needs a string 'id'")
    name = str(raw.get("name") or recipe_id)

    doneness: list[Doneness] = []
    for index, item in enumerate(raw.get("doneness") or []):
        if not isinstance(item, dict) or "target" not in item:
            raise RecipeError(f"{recipe_id}: doneness {index + 1} needs a 'target'")
        item_id = str(item.get("id") or f"level_{index + 1}")
        doneness.append(
            Doneness(
                id=item_id,
                name=str(item.get("name") or item_id.replace("_", " ").capitalize()),
                target=_number(item["target"], f"{recipe_id}: doneness '{item_id}' target"),
            )
        )

    target = _number(raw.get("target"), f"{recipe_id}: target", allow_none=True)
    if target is None and not doneness:
        raise RecipeError(f"{recipe_id}: needs a 'target' or a 'doneness' list")

    stages = tuple(_parse_stage(item, index) for index, item in enumerate(raw.get("stages") or []))
    seen: set[str] = set()
    for stage in stages:
        if stage.id in seen:
            raise RecipeError(f"{recipe_id}: duplicate stage id '{stage.id}'")
        seen.add(stage.id)

    return Recipe(
        id=recipe_id,
        name=name,
        category=str(raw.get("category") or "other"),
        grill_temp=_number(raw.get("grill_temp"), f"{recipe_id}: grill_temp", allow_none=True),
        carryover=_number(raw.get("carryover", 0), f"{recipe_id}: carryover") or 0.0,
        rest_minutes=_number(raw.get("rest_minutes", 0), f"{recipe_id}: rest_minutes") or 0.0,
        safe_min=_number(raw.get("safe_min"), f"{recipe_id}: safe_min", allow_none=True),
        target=target,
        doneness=tuple(doneness),
        stages=stages,
        notes=str(raw["notes"]) if raw.get("notes") else None,
        source=source,
    )


def build_stages(recipe: Recipe | None, *, rest_minutes: float | None = None) -> list[Stage]:
    """Stages for a cook: the recipe's own, or 'cook to pull temperature, then rest'."""
    if recipe is not None and recipe.stages:
        return list(recipe.stages)
    rest = rest_minutes if rest_minutes is not None else (recipe.rest_minutes if recipe else 0.0)
    stages = [Stage(id="cook", name="Cook", until_kind=UNTIL_MEAT, until_value=REF_PULL)]
    if rest and rest > 0:
        stages.append(
            Stage(id="rest", name="Rest", until_kind=UNTIL_MINUTES, until_value=float(rest), rest=True)
        )
    return stages
