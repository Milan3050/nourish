"""Typed contracts shared by agents, tools, API and evals.

Agents never exchange free text with each other: they publish these models into the graph state.
Numbers that matter (energy, macros, nutrients) are always produced by deterministic tools and
stored here; LLM-written text may refer to them but never invents them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Region(StrEnum):
    IN = "IN"  # India: IFCT 2017 food table, ICMR-NIN guidance
    US = "US"  # United States: USDA SR28 food table, DGA guidance


class Sex(StrEnum):
    male = "male"
    female = "female"


class Activity(StrEnum):
    sedentary = "sedentary"
    light = "light"
    moderate = "moderate"
    active = "active"
    very_active = "very_active"


class Goal(StrEnum):
    lose_weight = "lose_weight"
    maintain = "maintain"
    gain_muscle = "gain_muscle"
    gain_weight = "gain_weight"
    manage_condition = "manage_condition"
    general_health = "general_health"


class DietPattern(StrEnum):
    omnivore = "omnivore"
    eggetarian = "eggetarian"
    vegetarian = "vegetarian"  # lacto-vegetarian (Indian usage): dairy ok, no egg/meat/fish
    vegan = "vegan"
    pescatarian = "pescatarian"
    jain = "jain"  # vegetarian + no root vegetables, onion, garlic


class UserProfile(BaseModel):
    """What the user tells us. Validated at the API edge with hard physiological bounds."""

    user_id: str | None = None
    name: str | None = Field(default=None, max_length=80)
    age: int | None = Field(default=None, ge=1, le=120)
    sex: Sex | None = None
    height_cm: float | None = Field(default=None, ge=50, le=250)
    weight_kg: float | None = Field(default=None, ge=10, le=350)
    activity: Activity = Activity.light
    goal: Goal = Goal.general_health
    target_weight_kg: float | None = Field(default=None, ge=10, le=350)
    timeline_weeks: int | None = Field(default=None, ge=1, le=260)
    region: Region = Region.IN
    diet_pattern: DietPattern = DietPattern.omnivore
    religious_rules: list[Literal["halal", "kosher", "no_beef", "no_pork"]] = []
    allergies: list[str] = []
    intolerances: list[str] = []
    dislikes: list[str] = []
    conditions: list[str] = []
    medications: list[str] = []
    pregnant: bool = False
    breastfeeding: bool = False
    meals_per_day: int = Field(default=3, ge=1, le=6)
    budget: Literal["low", "medium", "high"] = "medium"
    cuisine_preferences: list[str] = []

    @field_validator("allergies", "intolerances", "dislikes", "conditions", "medications", mode="before")
    @classmethod
    def _normalise(cls, v: list[str] | str | None) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = [p for p in v.split(",")]
        return sorted({s.strip().lower() for s in v if s and s.strip()})


class MacroTargets(BaseModel):
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float


class NutrientLimits(BaseModel):
    sodium_mg_max: float = 2300
    added_sugar_g_max: float | None = None
    saturated_fat_g_max: float | None = None
    potassium_mg_max: float | None = None  # set for kidney disease / some medications
    vitamin_k_ug_consistent: bool = False  # warfarin: keep intake consistent, avoid spikes


class RiskFlag(BaseModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    message: str


class HealthProfile(BaseModel):
    """Health Assessment Agent output. Energy numbers come from the energy tools."""

    bmr_kcal: float
    tdee_kcal: float
    targets: MacroTargets
    limits: NutrientLimits = NutrientLimits()
    risk_flags: list[RiskFlag] = []
    escalation_required: bool = False
    assumptions: list[str] = []
    missing_info: list[str] = []
    rationale: str = ""


class Citation(BaseModel):
    chunk_id: str
    source: str
    title: str
    url: str | None = None
    snippet: str
    score: float | None = None


class EvidenceItem(BaseModel):
    question: str
    answer: str
    citations: list[Citation] = []
    sufficient: bool = True


class FoodPortion(BaseModel):
    food_code: str
    food_name: str
    grams: float = Field(gt=0, le=1500)


class NutritionTotals(BaseModel):
    kcal: float = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0
    fiber_g: float = 0
    sugar_g: float = 0
    saturated_fat_g: float = 0
    sodium_mg: float = 0
    potassium_mg: float = 0
    calcium_mg: float = 0
    iron_mg: float = 0
    vitamin_c_mg: float = 0
    vitamin_k_ug: float = 0

    def __add__(self, other: NutritionTotals) -> NutritionTotals:
        return NutritionTotals(**{k: getattr(self, k) + getattr(other, k) for k in type(self).model_fields})

    def rounded(self) -> NutritionTotals:
        return NutritionTotals(**{k: round(getattr(self, k), 1) for k in type(self).model_fields})


class Meal(BaseModel):
    slot: Literal["breakfast", "mid_morning", "lunch", "snack", "dinner", "bedtime"]
    name: str
    items: list[FoodPortion]
    notes: str = ""
    totals: NutritionTotals | None = None  # filled by the nutrition tool, never by the LLM


class DayPlan(BaseModel):
    day: int = Field(ge=1, le=14)
    meals: list[Meal]
    totals: NutritionTotals | None = None


class MealPlan(BaseModel):
    days: list[DayPlan]
    region: Region
    notes: str = ""
    version: int = 1


class Violation(BaseModel):
    rule_id: str
    severity: Literal["info", "warning", "critical"]
    message: str
    location: str | None = None  # e.g. "day 2 / lunch / almond butter"
    fix_hint: str | None = None


class ValidationReport(BaseModel):
    verdict: Literal["PASS", "REJECT", "ESCALATE"]
    violations: list[Violation] = []
    groundedness: float | None = None
    checked: list[str] = []  # names of checks that ran
    summary: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Recommendation(BaseModel):
    headline: str
    summary: str
    key_points: list[str] = []
    tips: list[str] = []
    citations: list[Citation] = []
    follow_up_questions: list[str] = []
    disclaimer: str = (
        "General nutrition information, not medical advice. Talk to a doctor or registered "
        "dietitian before major diet changes, especially with a medical condition or medication."
    )
