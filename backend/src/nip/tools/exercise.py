"""Exercise library and the deterministic workout rule engine.

The Workout Planning agent chooses exercises; these functions decide whether a plan fits the person
(equipment, experience, injuries, days, session time, weekly volume, aerobic minutes) and compute its
numbers (minutes, sets per muscle group, energy from MET x body weight x time). No number comes from
the model.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import yaml

from nip.domain.models import Goal, UserProfile

LIBRARY = Path(__file__).resolve().parents[4] / "data" / "exercises" / "exercises.json"
RULES = Path(__file__).resolve().parents[1] / "rules" / "rules.yaml"
LEVELS = ["beginner", "intermediate", "advanced"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@lru_cache
def library() -> dict[str, dict]:
    return {e["id"]: e for e in json.loads(LIBRARY.read_text())["exercises"]}


@lru_cache
def rules() -> dict:
    return yaml.safe_load(RULES.read_text())["workout"]


def training_goal(p: UserProfile) -> str:
    return {Goal.gain_muscle: "muscle_gain", Goal.gain_weight: "muscle_gain", Goal.lose_weight: "fat_loss"}.get(
        p.goal, "general_fitness")


def exercise_allowed(e: dict, p: UserProfile) -> tuple[bool, str | None]:
    if not set(e["equipment"]) & {*p.equipment, "none"}:
        return False, f"needs {' or '.join(e['equipment'])}"
    if LEVELS.index(e["level"]) > LEVELS.index(p.experience):
        return False, f"{e['level']} exercise for a {p.experience}"
    for inj in p.injuries:
        if any(c in inj or inj in c for c in e["cautions"]):
            return False, f"not advised with {inj}"
    return True, None


def search_exercises(profile: dict, query: str = "", pattern: str | None = None, muscle: str | None = None,
                     kind: str | None = None, limit: int = 12) -> dict:
    """Exercises this person can do (equipment, level, injuries already applied)."""
    p = UserProfile(**profile)
    q = set(re.findall(r"[a-z]+", query.lower()))
    out = []
    for e in library().values():
        if pattern and e["pattern"] != pattern or muscle and muscle not in e["muscles"] or kind and e["type"] != kind:
            continue
        if q and not q & set(re.findall(r"[a-z]+", f"{e['name']} {e['pattern']} {' '.join(e['muscles'])}".lower())):
            continue
        if exercise_allowed(e, p)[0]:
            out.append({k: e[k] for k in ("id", "name", "type", "pattern", "muscles")})
    return {"results": out[:limit]}


def _reps_mid(reps) -> float | None:
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(reps))]
    return sum(nums) / len(nums) if nums else None


def check_workout(profile: dict, workout: dict) -> dict:
    """Validate a weekly workout plan and compute its totals. Returns {verdict, violations, totals, sessions}."""
    p, r, lib = UserProfile(**profile), rules(), library()
    goal = training_goal(p)
    v: list[dict] = []
    sessions = workout.get("sessions") or []

    def add(rule_id, severity, message, location=None):
        v.append({"rule_id": rule_id, "severity": severity, "message": message, "location": location})

    days = [s.get("day") for s in sessions]
    if len(set(days)) != len(days):
        add("days_unique", "critical", f"days repeat ({days}); one session per weekday")
    if len(sessions) != p.training_days_per_week:
        add("training_days", "critical", f"{len(sessions)} sessions; the user trains {p.training_days_per_week} days a week")
    if p.training_days_per_week >= 7:
        add("rest_day", "warning", "no rest day in the week")
    sets_by_muscle: dict[str, float] = {}
    aerobic, kcal_week, out_sessions = 0.0, 0.0, []
    lo_reps, hi_reps = r["rep_ranges"][goal]
    for s in sessions:
        where = f"day {s.get('day')}"
        minutes, kcal = float(r["warmup_minutes"]), 0.0
        for x in s.get("exercises") or []:
            e = lib.get(x.get("exercise_id", ""))
            if e is None:
                add("unknown_exercise", "critical", f"unknown exercise_id {x.get('exercise_id')}", where)
                continue
            ok, why = exercise_allowed(e, p)
            if not ok:
                add("exercise_not_allowed", "critical", f"{e['name']}: {why}", where)
            if e["type"] == "strength":
                sets = int(x.get("sets") or 0)
                if not 1 <= sets <= r["max_sets_per_exercise"]:
                    add("sets", "critical", f"{e['name']}: {sets} sets (1-{r['max_sets_per_exercise']})", where)
                mid = _reps_mid(x.get("reps"))
                if mid is None:
                    add("reps", "critical", f"{e['name']}: reps missing", where)
                elif not lo_reps <= mid <= hi_reps and e["pattern"] not in ("core", "carry"):
                    add("reps", "warning", f"{e['name']}: {x.get('reps')} reps; {lo_reps}-{hi_reps} suits {goal.replace('_', ' ')}", where)
                rest = float(x.get("rest_s") or 60)
                ex_min = sets * (r["seconds_per_set"] + rest) / 60
                for m in e["muscles"]:
                    sets_by_muscle[m] = sets_by_muscle.get(m, 0) + sets * (1 if m == e["muscles"][0] else 0.5)
            else:
                ex_min = float(x.get("minutes") or 0)
                if ex_min <= 0:
                    add("minutes", "critical", f"{e['name']}: minutes missing", where)
                if e["type"] == "cardio":
                    aerobic += ex_min
            minutes += ex_min
            kcal += e["met"] * p.weight_kg * ex_min / 60 if p.weight_kg else 0
        if minutes > p.session_minutes * (1 + r["session_time_tolerance_pct"] / 100):
            add("session_time", "critical", f"about {minutes:.0f} min; the user has {p.session_minutes} min", where)
        kcal_week += kcal
        out_sessions.append({"day": s.get("day"), "minutes": round(minutes), "kcal": round(kcal)})
    if p.training_days_per_week:
        band = r["weekly_sets"][p.experience]
        strength_days = any(lib.get(x.get("exercise_id", ""), {}).get("type") == "strength"
                            for s in sessions for x in s.get("exercises") or [])
        if strength_days and goal in ("muscle_gain", "strength", "general_fitness", "fat_loss"):
            trainable = {e["muscles"][0] for e in lib.values() if e["type"] == "strength" and exercise_allowed(e, p)[0]}
            for m in r["major_muscles"]:
                n = sets_by_muscle.get(m, 0)
                if m not in trainable:
                    add("no_safe_exercise", "info",
                        f"{m}: no exercise in the library is advised with {', '.join(p.injuries)}; a physiotherapist "
                        "can suggest alternatives" if p.injuries else
                        f"{m}: no exercise in the library targets it with your equipment and level; more equipment "
                        "(dumbbells, bands) would add options")
                elif n < band[0] * (0.5 if goal != "muscle_gain" else 1):
                    add("weekly_volume", "warning" if goal != "muscle_gain" else "critical",
                        f"{m}: {n:.0f} sets a week; aim for {band[0]}-{band[1]}")
                elif n > band[1] * 1.5:
                    add("weekly_volume", "critical", f"{m}: {n:.0f} sets a week is far above {band[1]}")
        if aerobic < r["aerobic_minutes_week_min"] and goal in ("fat_loss", "general_fitness"):
            add("aerobic_minutes", "warning",
                f"{aerobic:.0f} min of cardio a week; WHO advises at least {r['aerobic_minutes_week_min']} min of moderate activity")
    verdict = "REJECT" if any(x["severity"] == "critical" for x in v) else "PASS"
    totals = {"sessions": len(sessions), "minutes_week": round(sum(s["minutes"] for s in out_sessions)),
              "aerobic_minutes_week": round(aerobic), "kcal_week": round(kcal_week),
              "sets_by_muscle": {k: round(n, 1) for k, n in sorted(sets_by_muscle.items())}}
    return {"verdict": verdict, "violations": v, "totals": totals, "sessions": out_sessions, "goal": goal}


def resolve_exercise(ref: str) -> dict | None:
    """The library exercise an id refers to: exact id, or the closest id/name by word overlap
    ('romanian deadlift' -> romanian_deadlift_db). Returns None when nothing is close."""
    lib = library()
    key = re.sub(r"[^a-z0-9]+", "_", str(ref).lower()).strip("_")
    if key in lib:
        return lib[key]
    words = set(key.split("_")) - {"", "db", "the"}
    best, score = None, 0.0
    for e in lib.values():
        cand = set(re.findall(r"[a-z0-9]+", f"{e['id']} {e['name']}".lower())) - {"db"}
        s = len(words & cand) / len(words | cand) if words else 0
        if s > score:
            best, score = e, s
    return best if score >= 0.5 else None


def _ex_minutes(x: dict, e: dict, r: dict) -> float:
    if e["type"] == "strength":
        return int(x.get("sets") or 0) * (r["seconds_per_set"] + float(x.get("rest_s") or 60)) / 60
    return float(x.get("minutes") or 0)


def repair_workout(profile: dict, workout: dict) -> dict:
    """Complete and rebalance a weekly plan deterministically, keeping the exercises the agent chose.

    Unknown or unsafe exercises are dropped; missing sets, reps, rest and minutes are filled from the rules;
    sessions are spread over the week (the rules' weekday schedule) to match the user's training days;
    muscle groups under their weekly range get more sets (or a safe library exercise); sessions over the
    time limit are trimmed. Every number comes from rules.yaml. The result still has to pass check_workout.
    """
    p, r, lib = UserProfile(**profile), rules(), library()
    goal = training_goal(p)
    lo, hi = r["rep_ranges"][goal]
    sessions: list[dict] = []
    for s in workout.get("sessions") or []:
        exercises = []
        for x in s.get("exercises") or []:
            e = resolve_exercise(x.get("exercise_id", ""))
            if e is None or not exercise_allowed(e, p)[0]:
                continue
            y = {"exercise_id": e["id"]}
            if e["type"] == "strength":
                y["sets"] = min(max(int(x.get("sets") or r["default_sets"]), 1), r["max_sets_per_exercise"])
                y["reps"] = str(x.get("reps") or f"{lo}-{hi}")
                y["rest_s"] = int(x.get("rest_s") or r["default_rest_s"][goal])
            else:
                y["minutes"] = float(x.get("minutes") or r["default_cardio_minutes"])
            if y["exercise_id"] not in {z["exercise_id"] for z in exercises}:
                exercises.append(y)
        if exercises:
            sessions.append({"focus": s.get("focus") or "Training", "exercises": exercises})
    n = p.training_days_per_week
    if not n or not sessions:
        return {"sessions": [], "progression": workout.get("progression", "")}
    k = len(sessions)
    sessions = [json.loads(json.dumps(sessions[i % k])) for i in range(n)]  # trim, or repeat to fill the days
    for s, day in zip(sessions, r["weekday_schedule"][n], strict=True):
        s["day"] = day

    limit = p.session_minutes * (1 + r["session_time_tolerance_pct"] / 100)

    def minutes(s: dict) -> float:
        return r["warmup_minutes"] + sum(_ex_minutes(x, lib[x["exercise_id"]], r) for x in s["exercises"])

    def weekly(m: str) -> float:
        return sum(x["sets"] * (1 if lib[x["exercise_id"]]["muscles"][0] == m else 0.5)
                   for s in sessions for x in s["exercises"]
                   if lib[x["exercise_id"]]["type"] == "strength" and m in lib[x["exercise_id"]]["muscles"])

    band = r["weekly_sets"][p.experience]
    allowed = [e for e in lib.values() if e["type"] == "strength" and exercise_allowed(e, p)[0]]
    for m in r["major_muscles"]:
        options = [e for e in allowed if e["muscles"][0] == m]
        if not options:
            continue
        for _ in range(40):  # raise volume one set (or one exercise) at a time until in range
            if weekly(m) >= band[0]:
                break
            grow = [(s, x) for s in sessions for x in s["exercises"] if lib[x["exercise_id"]]["muscles"][0] == m
                    and x["sets"] < r["max_sets_per_exercise"] and minutes(s) + _ex_minutes({**x, "sets": 1}, lib[x["exercise_id"]], r) <= limit]
            if grow:
                s, x = min(grow, key=lambda sx: minutes(sx[0]))
                x["sets"] += 1
                continue
            used = {x["exercise_id"] for s in sessions for x in s["exercises"]}
            e = next((o for o in options if o["id"] not in used), options[0])
            new = {"exercise_id": e["id"], "sets": r["default_sets"], "reps": f"{lo}-{hi}", "rest_s": r["default_rest_s"][goal]}
            fits = [s for s in sessions if minutes(s) + _ex_minutes(new, e, r) <= limit and e["id"] not in
                    {x["exercise_id"] for x in s["exercises"]}]
            if not fits:
                break
            min(fits, key=minutes)["exercises"].append(new)
        while weekly(m) > band[1] * 1.5:  # far above range: trim sets
            big = [x for s in sessions for x in s["exercises"] if lib[x["exercise_id"]]["muscles"][0] == m
                   and x["sets"] > r["min_sets_when_trimming"]]
            if not big:
                break
            max(big, key=lambda x: x["sets"])["sets"] -= 1
    for s in sessions:  # sessions over the time limit: shorten cardio, then remove sets with volume to spare
        while minutes(s) > limit:
            cardio = [x for x in s["exercises"] if "minutes" in x and x["minutes"] > r["min_cardio_minutes_when_trimming"]]
            if cardio:
                c = max(cardio, key=lambda x: x["minutes"])
                c["minutes"] = max(r["min_cardio_minutes_when_trimming"], c["minutes"] - 5)
                continue
            spare = [x for x in s["exercises"] if "sets" in x and x["sets"] > r["min_sets_when_trimming"]
                     and weekly(lib[x["exercise_id"]]["muscles"][0]) - 1 >= band[0]]
            pool = spare or [x for x in s["exercises"] if "sets" in x and x["sets"] > r["min_sets_when_trimming"]]
            if not pool:
                break
            max(pool, key=lambda x: x["sets"])["sets"] -= 1
    return {"sessions": sessions, "progression": workout.get("progression", "")}
