# Prep and form words

Data for the prep tier of the recipe-name cascade (`docs/spec/07`, 3C and the
1G amendment VS2). `app/recipes/prep.py` loads both files once, at first use,
and stays pure: no database, no network.

- `prep_words.txt`: words that say how a cook treats an ingredient (minced,
  peeled, finely, fresh, large). The tier strips them from the start and the
  end of a normalized recipe name, looks the remainder up exactly, and offers
  the hit with the stripped words as the proposal's note. A person choosing
  that proposal moves the words into each line's note ("minced; for the
  sauce"), the way `kerp recipes conform` does on the host.
- `form_words.txt`: words that are part of what is bought (ground, dried,
  canned, unsalted, whole). They are never stripped, so "ground cumin" is never
  offered cumin. The file exists so that the rule is data a reviewer can read,
  and so the loader can refuse a word listed in both.

One lowercase word per line, as `app/catalog/names.normalize_name` would write
it (hyphens inside a word stay). Blank lines and lines starting with `#` are
ignored. Only the first and last words of a name are ever stripped, one at a
time, and a name is never stripped to nothing: "minced" alone offers nothing.
Stripping never resolves a line on its own (VC3); it only proposes.
