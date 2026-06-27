"""Canonical вид судопроизводства (case type) codes — single source of truth.

A court-practice dataset is scraped per proceeding type: СУДРФ exposes each in a
separate table (``g1_case`` → гражданские, ``u1_case`` → уголовные, ``a1_case``
→ административные), and arbitration lives in a different system entirely. So the
case type is a dataset-level property, just like the region, and gets stored in
the ``case_type`` keyword field at index time.

Both the indexing pipeline (to resolve a dataset's case type from its catalog)
and the ``search_court_practice`` tool (to document the ``case_types`` parameter)
read from here, so the vocabulary never drifts between the two.
"""

# Stored verbatim in the ``case_type`` field and passed by the model to the
# ``case_types`` search filter. English codes (stable, easy to type) → Russian
# label shown in the tool's parameter reference.
CASE_TYPE_CODE_TO_NAME: dict[str, str] = {
    "civil": "гражданские",
    "criminal": "уголовные",
    "administrative": "административные",
    "arbitration": "арбитражные",
}

CASE_TYPE_NAME_TO_CODE: dict[str, str] = {
    name: code for code, name in CASE_TYPE_CODE_TO_NAME.items()
}

# СУДРФ ``delo_table`` (the authoritative machine marker in a dataset catalog's
# ``deloFilter``) → our case-type code. Civil=g1, criminal=u1, КАС/админ=a1.
DELO_TABLE_TO_CODE: dict[str, str] = {
    "g1_case": "civil",
    "u1_case": "criminal",
    "a1_case": "administrative",
}

# Human-readable "code — Название" list, used in the search tool's parameter doc.
CASE_TYPE_REFERENCE: str = ", ".join(
    f"{code} ({name})" for code, name in CASE_TYPE_CODE_TO_NAME.items()
)


def case_type_from_name(name: str | None) -> str | None:
    """Map a Russian case-type label (e.g. catalog ``category``) to its code."""
    if not isinstance(name, str):
        return None
    return CASE_TYPE_NAME_TO_CODE.get(name.strip().lower())


def case_type_from_catalog(catalog: dict | None) -> str | None:
    """Resolve a dataset's case type from its ``_catalog.json``.

    Prefers the machine-stable ``deloFilter.delo_table`` (u1_case/g1_case/...),
    falls back to the human ``category`` label ("уголовные"/"гражданские"/...).
    Returns None when neither is usable (the caller may then fall back to an
    explicit ``--case-type`` override).
    """
    if not isinstance(catalog, dict):
        return None
    delo_filter = catalog.get("deloFilter")
    if isinstance(delo_filter, dict):
        code = DELO_TABLE_TO_CODE.get(str(delo_filter.get("delo_table") or "").strip())
        if code:
            return code
    return case_type_from_name(catalog.get("category"))
