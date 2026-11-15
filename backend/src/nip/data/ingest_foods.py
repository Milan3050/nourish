"""Normalise the Indian (IFCT 2017) and US (USDA SR28) food tables into one schema.

Output: data/processed/foods.csv, one row per food with nutrients per 100 g edible portion and
explicit allergen / animal-source tags. Region is a column, so region-based selection is a filter.

    uv run python -m nip.data.ingest_foods --ifct data/raw/ifct2017/compositions.csv \
        --ifct-groups data/raw/ifct2017/groups.csv --usda data/raw/usda_sr28.sql

Sources and licences are listed in data/README.md.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

from nip.data.taxonomy import ALCOHOL_KEYWORDS, allergens_for, is_animal_flesh, meats_for

NUTRIENT_COLUMNS = [
    "kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "saturated_fat_g",
    "sodium_mg", "potassium_mg", "calcium_mg", "iron_mg", "vitamin_c_mg", "vitamin_k_ug",
]
OUT_COLUMNS = ["code", "region", "source", "name", "group", "allergens", "meats", "animal_flesh",
               "alcohol", *NUTRIENT_COLUMNS]

KJ_PER_KCAL = 4.184


def _f(v: str | None) -> float:
    if v is None or v == "" or v == "\\N":
        return 0.0
    try:
        x = float(v)
    except ValueError:
        return 0.0
    return 0.0 if x != x else x  # NaN -> 0


def _row(code: str, region: str, source: str, name: str, group: str, n: dict[str, float]) -> dict:
    allergens = allergens_for(name, group)
    meats = meats_for(name, group)
    lname = name.lower()
    return {
        "code": code, "region": region, "source": source, "name": name.strip(), "group": group,
        "allergens": "|".join(sorted(allergens)), "meats": "|".join(sorted(meats)),
        "animal_flesh": int(is_animal_flesh(allergens, meats)),
        "alcohol": int(any(re.search(rf"(?<![a-z]){re.escape(k)}", lname) for k in ALCOHOL_KEYWORDS)),
        **{k: round(n.get(k, 0.0), 4) for k in NUTRIENT_COLUMNS},
    }


def load_ifct(compositions: Path, groups: Path) -> list[dict]:
    """IFCT values are per 100 g: energy in kJ, macros in g, minerals and vitamins in g."""
    group_names = {r["code"]: r["group"] for r in csv.DictReader(groups.open(encoding="utf-8"))}
    rows = []
    for r in csv.DictReader(compositions.open(encoding="utf-8")):
        g = group_names.get(r["code"][0], r.get("grup", ""))
        sugar = _f(r.get("fsugar"))
        n = {
            "kcal": _f(r["enerc"]) / KJ_PER_KCAL,
            "protein_g": _f(r["protcnt"]),
            "carbs_g": _f(r["choavldf"]),
            "fat_g": _f(r["fatce"]),
            "fiber_g": _f(r["fibtg"]),
            "sugar_g": sugar,
            "saturated_fat_g": _f(r.get("fasat")),
            "sodium_mg": _f(r.get("na")) * 1000,
            "potassium_mg": _f(r.get("k")) * 1000,
            "calcium_mg": _f(r.get("ca")) * 1000,
            "iron_mg": _f(r.get("fe")) * 1000,
            "vitamin_c_mg": _f(r.get("vitc")) * 1000,
            "vitamin_k_ug": _f(r.get("vitk1")) * 1e6,
        }
        rows.append(_row(f"IN-{r['code']}", "IN", "IFCT2017", r["name"], g, n))
    return rows


USDA_NUTRIENTS = {
    "208": "kcal", "203": "protein_g", "205": "carbs_g", "204": "fat_g", "291": "fiber_g",
    "269": "sugar_g", "606": "saturated_fat_g", "307": "sodium_mg", "306": "potassium_mg",
    "301": "calcium_mg", "303": "iron_mg", "401": "vitamin_c_mg", "430": "vitamin_k_ug",
}
USDA_SKIP_GROUPS = {"0300", "2100", "3500", "3600"}  # baby foods, fast foods, AI/AN foods, restaurant


def _copy_blocks(sql_path: Path, wanted: set[str]) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = defaultdict(list)
    current: str | None = None
    with sql_path.open(encoding="utf-8") as fh:
        for line in fh:
            if current is None:
                if line.startswith("COPY "):
                    table = line.split()[1]
                    current = table if table in wanted else "__skip__"
                continue
            if line.startswith("\\."):
                current = None
                continue
            if current != "__skip__":
                out[current].append(line.rstrip("\n").split("\t"))
    return out


def load_usda(sql_path: Path) -> list[dict]:
    """SR28 values are per 100 g in the units of nutr_def (kcal, g, mg, ug)."""
    blocks = _copy_blocks(sql_path, {"fd_group", "food_des", "nut_data"})
    groups = {r[0]: r[1] for r in blocks["fd_group"]}
    nutrients: dict[str, dict[str, float]] = defaultdict(dict)
    for r in blocks["nut_data"]:
        key = USDA_NUTRIENTS.get(r[1])
        if key:
            nutrients[r[0]][key] = _f(r[2])
    rows = []
    for r in blocks["food_des"]:
        ndb, grp, long_desc = r[0], r[1], r[2]
        if grp in USDA_SKIP_GROUPS or ndb not in nutrients:
            continue
        rows.append(_row(f"US-{ndb}", "US", "USDA-SR28", long_desc, groups.get(grp, ""), nutrients[ndb]))
    return rows


def retag(path: Path) -> list[tuple[str, str, list[str]]]:
    """Re-derive allergen tags on an existing processed table after a taxonomy change, without the raw
    files. New tags are added to the existing ones, never removed (tagging favours recall)."""
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    changed = []
    for r in rows:
        old = {a for a in r["allergens"].split("|") if a}
        new = old | allergens_for(r["name"], r["group"])
        if new != old:
            r["allergens"] = "|".join(sorted(new))
            changed.append((r["code"], r["name"], sorted(new - old)))
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retag", type=Path, help="re-derive allergen tags on a processed foods.csv in place")
    ap.add_argument("--ifct", type=Path)
    ap.add_argument("--ifct-groups", type=Path)
    ap.add_argument("--usda", type=Path)
    ap.add_argument("--out", type=Path, default=Path("data/processed/foods.csv"))
    a = ap.parse_args()
    if a.retag:
        for code, name, added in retag(a.retag):
            print(f"{code} {name}: +{added}")
        return
    rows: list[dict] = []
    if a.ifct:
        rows += load_ifct(a.ifct, a.ifct_groups)
    if a.usda:
        rows += load_usda(a.usda)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    by_region: dict[str, int] = defaultdict(int)
    for r in rows:
        by_region[r["region"]] += 1
    print(f"wrote {len(rows)} foods to {a.out}: {dict(by_region)}")


if __name__ == "__main__":
    main()
