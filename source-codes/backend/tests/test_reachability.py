"""2-T9 — post-sweep reachability: no dead code without a keep-reason.

After the Phase-2 sweep (plan 2.10), every module that exists in the mounted
application's import surface must be either reachable from `main.py` or carry
a recorded keep-reason in docs/0.4.0/08-helper-layer.md. This test bites: add
an unmounted router file and it fails.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# Modules that are deliberately kept although not reachable from the mounted
# app. Every entry needs a reason; Phase 3 empties most of this list.
KEEP_REASONS = {
    # ai/test_manager.py and ai/dict_ingest.py: deleted in Phase 3 with the
    # verify_plan8.py rebuild (their keep-reasons expired).
    "ai/effort.py": "imported (relative) by ai/bayes.py, credit_risk_domain.py, db_understanding.py, rca_checker.py — modules ai/skills.py lazy-loads for default prompts",
    "ai/skill_form.py": "imported by ai/skills.py save-path; skills is live via seeds",
    "verify_plan8.py": "0.4.0 validation gate (plan 1.4), rebuilt in Phase 3 against the new register; run standalone, not imported by the app",
    "spike_gx.py": "PSI parity evidence, needed at PSI enablement (backlog B2); broken at baseline (F-01)",
    "spike_integration.py": "PSI parity evidence (backlog B2); broken at baseline (F-01)",
    "spike_metrics.py": "PSI parity evidence (backlog B2)",
    "spike_recalibrate.py": "PSI parity evidence (backlog B2); broken at baseline (F-01)",
}


def test_every_router_file_is_mounted():
    """Every APIRouter module must be reachable directly or as a sub-router."""
    main_src = (BACKEND / "main.py").read_text(encoding="utf-8")
    mounted = set(re.findall(r"app\.include_router\((\w+)\.router\)", main_src))
    imported = set()
    m = re.search(r"from routers import ([\w, ]+)", main_src)
    if m:
        imported = {s.strip() for s in m.group(1).split(",")}
    assert mounted, "no routers mounted — main.py changed shape"
    assert mounted <= imported

    router_dir = BACKEND / "routers"
    router_modules = {
        path.stem
        for path in router_dir.glob("*.py")
        if re.search(r"\brouter\s*=\s*APIRouter\(", path.read_text(encoding="utf-8"))
    }
    reachable = set(mounted)
    pending = list(mounted)
    while pending:
        module = pending.pop()
        source = (router_dir / f"{module}.py").read_text(encoding="utf-8")
        children = set(re.findall(r"router\.include_router\((\w+)\.router\)", source))
        pending.extend(children - reachable)
        reachable.update(children)

    unmounted = router_modules - reachable
    assert not unmounted, (
        f"unmounted router files with no keep-reason: {sorted(unmounted)} — "
        "mount them, delete them with evidence, or record a keep-reason (plan 2.10)"
    )


def test_keep_reasons_are_recorded_in_the_helper_doc():
    """Every KEEP_REASONS module is named in docs/0.4.0/08-helper-layer.md."""
    doc = BACKEND.parent / "docs" / "0.4.0" / "08-helper-layer.md"
    assert doc.exists(), "docs/0.4.0/08-helper-layer.md missing"
    text = doc.read_text(encoding="utf-8")
    missing = [mod for mod in KEEP_REASONS if Path(mod).name not in text]
    assert not missing, f"keep-reasons not recorded in 08-helper-layer.md: {missing}"


def test_kept_dead_modules_still_exist():
    """The keep-list must not silently rot: if a kept module is deleted, drop the row."""
    gone = [mod for mod in KEEP_REASONS if not (BACKEND / mod).exists()]
    assert not gone, f"KEEP_REASONS entries for deleted files (remove the rows): {gone}"
