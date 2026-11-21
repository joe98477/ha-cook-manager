"""Cook engine: pure Python, no Home Assistant imports.

Everything in this package can be imported and tested without Home Assistant.
Temperatures are degrees Celsius, times are Unix seconds.
"""

from .cook import Cook, CookEvent, CookStatus
from .eta import Estimate, estimate_remaining
from .recipes import Doneness, Recipe, RecipeError, Stage, build_stages, parse_recipe

__all__ = [
    "Cook",
    "CookEvent",
    "CookStatus",
    "Doneness",
    "Estimate",
    "Recipe",
    "RecipeError",
    "Stage",
    "build_stages",
    "estimate_remaining",
    "parse_recipe",
]
