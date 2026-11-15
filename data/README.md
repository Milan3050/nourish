# Data

| Region | Table | Foods | Source | Licence |
|---|---|---|---|---|
| IN | Indian Food Composition Tables 2017 (IFCT) | 542 | National Institute of Nutrition, Hyderabad, via the open dataset at github.com/nodef/ifct2017 | Data © NIN; packaging repo AGPL-3.0. Fine for this demo; check terms before commercial use. |
| US | USDA National Nutrient Database for Standard Reference, Release 28 (SR28) | 7,785 (baby, fast-food and restaurant groups dropped) | USDA ARS, via github.com/Adyg/usdanl-sr28-postgresql | US government work, public domain |

`processed/foods.csv` is committed so the app runs without the raw files. To rebuild it:

```bash
make data   # downloads the USDA dump (47 MB, not committed) and re-runs the ingestion
```

All nutrient values are per 100 g edible portion. Energy is kcal (IFCT kJ converted at 4.184).
Minerals in mg, vitamin K in µg. Allergen and animal-source tags come from `nip/data/taxonomy.py`
and deliberately favour recall: when unsure, a food is tagged.

`corpus/` holds the guideline documents the Nutrition Knowledge agent retrieves from, one
markdown file per source with front-matter (source, title, url, region, published).
