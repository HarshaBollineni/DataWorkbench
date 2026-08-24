"""RCA Stage 1 — bootstrap tenant/feature-flag rows and the governed
taxonomy seed (dimensions/values/version). See docs/rca/00-contracts.md
§1, §7, §8. Idempotent — safe to call every boot, like seed_dq_framework()."""
from __future__ import annotations

BOOTSTRAP_TENANT = "bootstrap"

# All default off; each stage's own gate flips exactly the flag it owns
# (contracts.md §8) — never ahead of that stage's gate passing.
FEATURE_FLAGS = [
    "TAXONOMY_ENABLED",
    "KB_MODULE_ENABLED",
    "RCA_ENABLED",
    "RCA_LEGACY_CREATION_RETIRED",
]

# migration plan §5.1 — every dimension is multi-select.
TAXONOMY_DIMENSIONS = [
    ("risk_type", "Risk type", [
        ("credit", "Credit"), ("market", "Market"), ("operational", "Operational"),
        ("liquidity", "Liquidity"), ("interest_rate", "Interest rate"),
    ]),
    ("portfolio", "Portfolio", [
        ("retail", "Retail"), ("small_enterprise", "Small enterprise"),
        ("large_corporate", "Large corporate"), ("sovereign", "Sovereign"),
    ]),
    ("product", "Product", [
        ("mortgage", "Mortgage"), ("auto", "Auto"), ("credit_card", "Credit card"),
        ("personal_loan", "Personal loan"), ("working_capital", "Working capital"),
        ("trade_finance", "Trade finance"), ("term_deposits", "Term deposits"),
        ("non_maturity_deposits", "Non-maturity deposits"),
        # TAX-01 (0.5.0, A-Q05) — Commercial Real Estate. Product dimension
        # ONLY, per the requester's note; Portfolio is deliberately untouched
        # (adding an unasked-for Portfolio value would change tag semantics
        # for existing assessments). Purely additive: the seeder upserts on
        # this natural-key id, so no migration and no taxonomy version bump.
        ("cre", "CRE"),
    ]),
    ("use_case", "Use case", [
        ("irb", "IRB"), ("ifrs9", "IFRS 9"), ("origination", "Origination"),
        ("adjudication", "Adjudication"), ("prepayment", "Prepayment"),
        ("deposit_runoff", "Deposit run-off"),
    ]),
]

TAXONOMY_VERSION_ID = f"taxv_{BOOTSTRAP_TENANT}_1"


# A flag's initial-seed value is 1 only once its owning stage's gate has
# passed (contracts.md §8) — updated here as each stage closes, never ahead
# of that stage. TAXONOMY_ENABLED flips with Stage 1's gate below; the rest
# stay 0 until Stage 2/3/7 close theirs.
_INITIAL_FLAG_STATE = {
    "TAXONOMY_ENABLED": 1,  # Stage 1 gate passed: browser UAT covers upload/
                            # assign/inherit/display (ui/e2e/taxonomy-tags.spec.js).
    "KB_MODULE_ENABLED": 1,  # Stage 2 gate passed: browser UAT covers upload/
                             # convert/review/publish (ui/e2e/knowledge-base.spec.js).
    "RCA_ENABLED": 1,  # Stage 3 gate passed: the vertical slice (case ->
                           # ... -> closure) is implemented and tested; new
                           # issues route to RCA (contracts.md §2).
    "RCA_LEGACY_CREATION_RETIRED": 1,  # Stage 7 gate passed (MP §17
                           # acceptance gate — see docs/rca/07-rollout.md):
                           # full backend/frontend/security/browser suite
                           # green, all four closing states reachable,
                           # migration verified idempotent. New legacy-v1
                           # issues can never be created again, even if
                           # RCA_ENABLED were later toggled off
                           # (ai/v2/issues.py:sync_issues).
}


def seed_platform() -> int:
    """Bootstrap tenant row + all feature-flag rows (initial state per stage
    gates closed so far; see _INITIAL_FLAG_STATE)."""
    import system_db as s
    n = 0
    s.upsert("tenants", {"tenant_id": BOOTSTRAP_TENANT, "name": "Bootstrap",
                         "created_at": s.now_ist()})
    n += 1
    for key in FEATURE_FLAGS:
        existing = s.query_one("feature_flags", key=key, tenant_id=BOOTSTRAP_TENANT)
        if existing:
            continue  # never clobber an operator's flag flip on reseed
        s.insert("feature_flags", {
            "key": key, "tenant_id": BOOTSTRAP_TENANT,
            "enabled": _INITIAL_FLAG_STATE.get(key, 0),
            "updated_at": s.now_ist(),
        })
        n += 1
    return n


def seed_taxonomy() -> int:
    """Seed the governed taxonomy: one version + the four initial dimensions/
    values from the migration plan (§5.1). Upsert on deterministic natural-key
    IDs so this is safe to call on every boot without duplicating rows."""
    import system_db as s
    n = 0
    if not s.query_one("tag_taxonomy_versions", version_id=TAXONOMY_VERSION_ID):
        s.insert("tag_taxonomy_versions", {
            "version_id": TAXONOMY_VERSION_ID, "tenant_id": BOOTSTRAP_TENANT,
            "seq": 1, "published_at": s.now_ist(),
        })
        n += 1
    for dim_key, dim_label, values in TAXONOMY_DIMENSIONS:
        dimension_id = f"dim_{dim_key}"
        s.upsert("tag_dimensions", {
            "dimension_id": dimension_id, "tenant_id": BOOTSTRAP_TENANT,
            "key": dim_key, "label": dim_label, "created_at": s.now_ist(),
        })
        n += 1
        for val_key, val_label in values:
            s.upsert("tag_values", {
                "value_id": f"val_{dim_key}_{val_key}", "dimension_id": dimension_id,
                "key": val_key, "label": val_label, "deprecated": 0,
                "created_at": s.now_ist(),
            })
            n += 1
    return n
