"""RCA Stage 2 — synthetic Knowledge Base loader. TEST-ONLY.

Deliberately NOT called from main.py or seeds/__init__.py:seed_all() /
seed_platform_and_taxonomy() — a normal product boot must never load this
package (docs/rca/00-contracts.md §6, migration plan §6). Call
load_synthetic_kb() explicitly from a test or a UAT fixture setup step only.

Reads synthetic-kb/manifest.yaml (repo root) — a YAML import/export manifest
per the migration plan's "YAML for manifests, taxonomy seeds, and synthetic
fixtures" rule (Markdown carries the rule content; the manifest carries the
per-rule state directives needed to demonstrate expiry/staleness/suspicion
without literally waiting months, which the normal upload->submit->publish UI
flow cannot do).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import yaml

import kb
import system_db as s

_ROOT = Path(__file__).resolve().parent.parent.parent  # source-codes/
PACKAGE_DIR = _ROOT / "synthetic-kb"


def _backdated_iso(months_ago: float) -> str:
    now = datetime.fromisoformat(s.now_ist())
    return (now - timedelta(days=months_ago * 30.436875)).isoformat()


def load_synthetic_kb(tenant_id: str, actor: str = "synthetic-kb-loader") -> dict:
    """Idempotent-ish for test convenience: re-running creates fresh documents
    (uploads are versioned by content hash, not by name), so tests that call
    this should use a scratch tenant or accept duplicate synthetic documents
    across repeated calls within one process — callers doing this in a shared
    system_state.db should call it at most once per run."""
    manifest = yaml.safe_load((PACKAGE_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    if not manifest.get("test_only"):
        raise RuntimeError("synthetic-kb/manifest.yaml must declare test_only: true")

    created = {"documents": 0, "rules_published": 0, "rules_left_draft": 0}
    for doc_spec in manifest["documents"]:
        path = PACKAGE_DIR / doc_spec["file"]
        content = path.read_bytes()
        upload = kb.upload_document(tenant_id, doc_spec["file"], "text/markdown", content,
                                    doc_spec.get("category_hint"), actor, is_synthetic=True)
        created["documents"] += 1
        version_id = upload["version"]["version_id"]
        kb.submit_for_review(tenant_id, version_id, actor, doc_spec.get("category_hint"))

        sections = s.query("kb_sections", version_id=version_id)
        by_heading = {sec["heading"]: sec for sec in sections}
        for heading, directive in doc_spec.get("rules", {}).items():
            sec = by_heading.get(heading)
            if not sec:
                raise RuntimeError(f"Manifest references unknown heading {heading!r} in {doc_spec['file']}")
            rule = s.query_one("kb_rules", section_id=sec["section_id"])
            if directive.get("lifecycle_state") == "draft":
                created["rules_left_draft"] += 1
                continue  # stays exactly as submit_for_review left it — never published
            published = kb.publish_rule(
                tenant_id, rule["rule_id"], actor, roles=["kb_reviewer"],
                category=directive["category"], trust_level=directive.get("trust_level", "human_confirmed"),
                shelf_life_months=directive.get("shelf_life_months"),
            )
            months_ago = directive.get("last_confirmed_months_ago")
            if months_ago:
                backdated = _backdated_iso(months_ago)
                s.update("kb_rules", {"rule_id": rule["rule_id"]},
                         {"last_confirmed_date": backdated, "effective_date": backdated})
            if directive.get("under_suspicion"):
                kb.mark_under_suspicion(tenant_id, rule["rule_id"],
                                        directive.get("under_suspicion_reason", "synthetic demonstration"))
            created["rules_published"] += 1
    return created
