#!/usr/bin/env python3
"""Profile a USDA FoodData Central full CSV download for ingredient seeding.

Reference code from the ingredient-vocabulary handoff (docs/spec/12-15). The
application and CI never run it. Reads straight from the zip, stdlib only:

    python3 tools/fdc_profile.py data/usda/FoodData_Central_csv_<date>.zip

Writes, next to the zip:
  fdc_profile.md      summary report
  fdc_candidates.csv  every Foundation and SR Legacy food, plus FNDDS-only
                      ingredient codes, ranked by FNDDS ingredient usage, with
                      FoodOn name and id, USDA common name and scientific name

v2 reads input_food.fdc_of_input_food and resolves FNDDS ingredient codes via
fndds_ingredient_nutrient_value.csv. It skips food_nutrient.csv, which naming
work does not need. Expect a few minutes: branded_food.csv is large.
"""

import collections
import csv
import io
import sys
import time
import zipfile
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit(
        "usage: python3 tools/fdc_profile.py data/usda/FoodData_Central_csv_<date>.zip"
    )
ZIP = Path(sys.argv[1]).expanduser()
OUT_MD = ZIP.with_name("fdc_profile.md")
OUT_CSV = ZIP.with_name("fdc_candidates.csv")
csv.field_size_limit(2**31 - 1)
CORE = ("foundation_food", "sr_legacy_food")
T0 = time.time()

zf = zipfile.ZipFile(ZIP)
members = {
    Path(i.filename).name: i
    for i in zf.infolist()
    if i.filename.lower().endswith(".csv")
}


def log(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", file=sys.stderr, flush=True)


def _open(name):
    return io.TextIOWrapper(
        zf.open(members[name]), encoding="utf-8", errors="replace", newline=""
    )


def rows(name):
    if name not in members:
        log(f"missing {name}, skipping")
        return
    log(f"reading {name}")
    with _open(name) as f:
        yield from csv.DictReader(f)


def header(name):
    with _open(name) as f:
        return next(csv.reader(f), [])


def g(r, *keys):
    for k in keys:
        if k in r and r[k] not in (None, ""):
            return r[k].strip()
    return ""


out = []


def p(s=""):
    out.append(s)


p(f"# FDC profile: `{ZIP.name}`\n")

# 1. Members + headers
p("## Files in the zip\n")
p("| file | uncompressed MB | columns |\n|---|---:|---|")
foodon_headers = []
for name in sorted(members):
    info = members[name]
    try:
        cols = header(name)
    except (OSError, csv.Error, UnicodeDecodeError) as e:
        cols = [f"(error: {e})"]
    if any("foodon" in c.lower() for c in cols):
        foodon_headers.append(name)
    p(f"| {name} | {info.file_size / 1e6:,.1f} | {', '.join(cols)} |")
p()

# 2. food.csv
dtype = collections.Counter()
food = {}
for r in rows("food.csv"):
    dt = g(r, "data_type")
    dtype[dt] += 1
    if dt != "branded_food":
        food[g(r, "fdc_id")] = (dt, g(r, "description"), g(r, "food_category_id"))
p("## Foods by data_type\n")
p("| data_type | rows |\n|---|---:|")
for k, v in dtype.most_common():
    p(f"| {k} | {v:,} |")
p()

cat = {g(r, "id"): g(r, "description") for r in rows("food_category.csv")}

ndb_to_fdc = {}
for r in rows("sr_legacy_food.csv"):
    ndb_to_fdc[g(r, "NDB_number", "ndb_number")] = g(r, "fdc_id")

# 3. FNDDS input usage (how often each food is used as an ingredient in survey-food recipes)
code_to_fdc = {}
code_desc = {}
for r in rows("fndds_ingredient_nutrient_value.csv"):
    c = g(r, "ingredient code")
    fid = g(r, "FDC ID")
    code_desc.setdefault(c, g(r, "Ingredient description"))
    if fid in food:
        code_to_fdc.setdefault(c, fid)

use = collections.Counter()
use_desc = {}
parents_with_inputs = set()
if "input_food.csv" in members:
    for r in rows("input_food.csv"):
        parent = food.get(g(r, "fdc_id"))
        if not parent or parent[0] != "survey_fndds_food":
            continue
        parents_with_inputs.add(g(r, "fdc_id"))
        key = g(r, "fdc_id_of_input_food", "fdc_of_input_food")
        if key not in food:
            sr = g(r, "sr_code")
            key = (
                ndb_to_fdc.get(sr)
                or code_to_fdc.get(sr)
                or (f"code:{sr}" if sr else "")
            )
        if not key:
            continue
        use[key] += 1
        use_desc.setdefault(key, g(r, "sr_description"))

p("## FNDDS recipe inputs\n")
p(f"- FNDDS foods with input rows: {len(parents_with_inputs):,}")
p(f"- distinct input foods referenced: {len(use):,}")
by_dt = collections.Counter(food[k][0] if k in food else "unresolved code" for k in use)
for k, v in by_dt.most_common():
    p(f"  - {k}: {v:,}")
p()

# 4. Category breakdown for core foods, with usage thresholds
p("## Foundation + SR Legacy by food_category (with FNDDS usage)\n")
p(
    "| category | foundation | foundation used ≥1 | sr_legacy | SR used ≥1 | SR used ≥5 | SR used ≥20 |\n|---|---:|---:|---:|---:|---:|---:|"
)
agg = collections.defaultdict(lambda: [0, 0, 0, 0, 0, 0])
for fid, (dt, desc, cid) in food.items():
    if dt not in CORE:
        continue
    a = agg[cat.get(cid, f"(id {cid})")]
    u = use.get(fid, 0)
    if dt == "foundation_food":
        a[0] += 1
        a[1] += u >= 1
    else:
        a[2] += 1
        a[3] += u >= 1
        a[4] += u >= 5
        a[5] += u >= 20
tot = [0] * 6
for c in sorted(agg, key=lambda c: -(agg[c][0] + agg[c][2])):
    a = agg[c]
    tot = [x + y for x, y in zip(tot, a)]
    p("| " + " | ".join([c] + [str(x) for x in a]) + " |")
p("| **total** | " + " | ".join(str(x) for x in tot) + " |\n")

p("## Top 150 most-used FNDDS inputs\n")
p("| uses | data_type | category | description |\n|---:|---|---|---|")
for k, n in use.most_common(150):
    dt, desc, cid = food.get(k, ("?", use_desc.get(k, k), ""))
    p(f"| {n} | {dt} | {cat.get(cid, '')} | {desc} |")
p()

# 5. Attributes / FoodOn
p("## Food attributes (core foods) and FoodOn\n")
p(f"- CSV files with a FoodOn-named column: {', '.join(foodon_headers) or 'none'}")
atype = (
    {g(r, "id"): g(r, "name") for r in rows("food_attribute_type.csv")}
    if "food_attribute_type.csv" in members
    else {}
)
attr = collections.Counter()
foodon_n = 0
foodon_ex = []
meta = collections.defaultdict(dict)
for r in rows("food_attribute.csv") or []:
    blob = " ".join(r.values() if r else []).lower()
    if "foodon" in blob:
        foodon_n += 1
        if len(foodon_ex) < 10:
            foodon_ex.append(r)
    f = food.get(g(r, "fdc_id"))
    if f and f[0] in CORE:
        tname = atype.get(g(r, "food_attribute_type_id"), "?")
        nm = g(r, "name")
        val = g(r, "value")
        attr[(f[0], tname, nm)] += 1
        m = meta[g(r, "fdc_id")]
        if tname == "Common Name":
            m.setdefault("common_name", val)
        elif nm.startswith("FoodOn Ontology Name"):
            m.setdefault("foodon_name", val)
        elif nm.startswith("FoodOn Ontology ID"):
            m.setdefault("foodon_id", val.rsplit("/", 1)[-1])
        elif nm == "Scientific Name":
            m.setdefault("scientific_name", val)
p(f"- food_attribute rows mentioning FoodOn: {foodon_n:,}")
for r in foodon_ex:
    p(f"  - `{dict(r)}`")
p("\n| data_type | attribute type | name | rows |\n|---|---|---|---:|")
for (dt, t, n), v in attr.most_common(40):
    p(f"| {dt} | {t} | {n} | {v:,} |")
p()

# 6. Portions (volume -> gram data)
mu = (
    {g(r, "id"): g(r, "name") for r in rows("measure_unit.csv")}
    if "measure_unit.csv" in members
    else {}
)
nport = collections.Counter()
units = collections.Counter()
for r in rows("food_portion.csv") or []:
    fid = g(r, "fdc_id")
    f = food.get(fid)
    if not f or f[0] not in CORE:
        continue
    nport[fid] += 1
    u = mu.get(g(r, "measure_unit_id"), "")
    if u in ("", "undetermined"):
        u = "modifier: " + (g(r, "modifier", "portion_description")[:40] or "?")
    units[u] += 1
core_ids = [k for k, v in food.items() if v[0] in CORE]
p("## Portions (gram weights) for core foods\n")
p(
    f"- core foods with ≥1 portion: {sum(1 for k in core_ids if nport[k]):,} of {len(core_ids):,}"
)
p(
    f"- SR foods used in FNDDS with ≥1 portion: {sum(1 for k in core_ids if nport[k] and use.get(k)):,} of {sum(1 for k in core_ids if use.get(k)):,}"
)
p("\n| unit / modifier | rows |\n|---|---:|")
for u, v in units.most_common(30):
    p(f"| {u} | {v:,} |")
p()

# 7. Branded
bc = collections.Counter()
nb = 0
ngtin = 0
for r in rows("branded_food.csv") or []:
    nb += 1
    ngtin += bool(g(r, "gtin_upc"))
    bc[g(r, "branded_food_category") or "(blank)"] += 1
p("## Branded foods\n")
p(f"- rows: {nb:,}; with gtin_upc: {ngtin:,}; distinct categories: {len(bc):,}")
p("\n| branded_food_category | rows |\n|---|---:|")
for c, v in bc.most_common(40):
    p(f"| {c} | {v:,} |")
p()

# 8. Candidate CSV
with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(
        [
            "fdc_id",
            "data_type",
            "category",
            "fndds_uses",
            "n_portions",
            "description",
            "common_name",
            "foodon_name",
            "foodon_id",
            "scientific_name",
        ]
    )
    for k in sorted(
        core_ids,
        key=lambda k: (-use.get(k, 0), food[k][0] != "foundation_food", food[k][1]),
    ):
        dt, desc, cid = food[k]
        m = meta.get(k, {})
        w.writerow(
            [
                k,
                dt,
                cat.get(cid, ""),
                use.get(k, 0),
                nport[k],
                desc,
                m.get("common_name", ""),
                m.get("foodon_name", ""),
                m.get("foodon_id", ""),
                m.get("scientific_name", ""),
            ]
        )
    # FNDDS-only ingredient codes (no FDC record) - e.g. "Vegetable oil, NFS", "Olive oil"
    for k, n in use.most_common():
        if k.startswith("code:"):
            c = k[5:]
            w.writerow(
                [
                    k,
                    "fndds_ingredient_code",
                    "",
                    n,
                    0,
                    code_desc.get(c) or use_desc.get(k, ""),
                    "",
                    "",
                    "",
                    "",
                ]
            )

OUT_MD.write_text("\n".join(out), encoding="utf-8")
log(f"done -> {OUT_MD} and {OUT_CSV}")
