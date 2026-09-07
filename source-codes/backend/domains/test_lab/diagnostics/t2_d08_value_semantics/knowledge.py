"""Production-owned knowledge resources for T2-D08 value semantics."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .matching import prepare_role_matcher
from .resources import load_terminology, load_value_semantics_kb

BACKEND_DIR = Path(__file__).resolve().parents[4]
VALUE_KB_V01_PATH = BACKEND_DIR / "knowledge_base" / "value_semantics_kb_v0_1.yaml"
VALUE_KB_PATH = BACKEND_DIR / "knowledge_base" / "value_semantics_kb_v0_2.yaml"
TERMINOLOGY_V02_PATH = BACKEND_DIR / "knowledge_base" / "credit_risk_abbreviations_v0_2.yaml"
TERMINOLOGY_PATH = BACKEND_DIR / "knowledge_base" / "credit_risk_abbreviations_v0_3.yaml"
PROMPT_PATH = BACKEND_DIR / "ai" / "agents" / "value_semantics_role_adjudication_v0_2.txt"

VALUE_DOCUMENT_ID = "kbdoc_t2d08_value_semantics"
VALUE_VERSION_V01_ID = "kbver_t2d08_value_semantics_v0_1"
VALUE_VERSION_ID = "kbver_t2d08_value_semantics_v0_2"
TERMINOLOGY_DOCUMENT_ID = "kbdoc_credit_risk_terminology"
TERMINOLOGY_VERSION_V02_ID = "kbver_credit_risk_terminology_v0_2"
TERMINOLOGY_VERSION_ID = "kbver_credit_risk_terminology_v0_3"


@lru_cache(maxsize=1)
def resources() -> tuple[dict[str, Any], dict[str, Any], Any]:
    value_kb = load_value_semantics_kb(VALUE_KB_PATH)
    terminology = load_terminology(TERMINOLOGY_PATH)
    return value_kb, terminology, prepare_role_matcher(value_kb, terminology)


def prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def role_index() -> dict[str, dict[str, Any]]:
    return {row["role"]: row for row in resources()[0]["semantic_roles"]}


def render_value_kb_markdown(document: dict[str, Any], source_name: str) -> str:
    metadata = document["metadata"]
    roles = document["semantic_roles"]
    rules = document["rules"]
    lines = [
        f"# Test 2, Diagnostic 8 — Value Semantics Knowledge Base v{metadata['version']}",
        "",
        str(metadata.get("purpose") or "Classify governed cell semantics."),
        "",
        f"**Source:** `{source_name}` (approved, source-controlled system knowledge)",
        "",
        "This diagnostic assigns only `CENSORED`, `STALE_FROZEN`, and "
        "`NOT_APPLICABLE`. `NO_TAG`, `UNCLASSIFIED`, and `UNSCOPED` are "
        "execution outcomes, not cell tags.",
        "",
        "PD, LGD, and EAD are optional context labels. Rules are activated by "
        "confirmed semantic roles and runtime declarations, not by a model label alone.",
        "",
        "## Semantic roles",
        "",
    ]
    for role in roles:
        representations = ", ".join(role.get("representations") or [])
        lines.append(
            f"- **{role['role']}** — {role['definition']} "
            f"Representations: {representations}."
        )
    lines.extend(["", "## Classification rules", ""])
    for rule in rules:
        lines.extend([
            f"### {rule['rule']} — {rule['tag_assigned']}",
            "",
            str(rule.get("definition") or rule.get("description") or ""),
            "",
        ])
        for entry in rule.get("entries") or []:
            prerequisites = [
                *(entry.get("indicator_roles") or []),
                *(entry.get("required_declarations") or []),
                *(entry.get("indicator_declarations") or []),
            ]
            lines.append(
                f"- `{entry['entry']}` targets `{entry['target_role']}`"
                + (f"; prerequisites: {', '.join(prerequisites)}" if prerequisites else "")
            )
        lines.append("")
    lines.extend([
        "## Interpretation boundary",
        "",
        "A tag is analytical treatment evidence, not automatically a data defect. "
        "Only grouped stale/frozen evidence, populated not-applicable values, or "
        "confirmed domain defects are candidates for issue promotion. Configuration "
        "gaps remain configuration findings.",
    ])
    return "\n".join(lines).strip() + "\n"


def render_terminology_markdown(document: dict[str, Any], source_name: str) -> str:
    metadata = document["metadata"]
    lines = [
        f"# Credit Risk Abbreviation and Terminology Dictionary v{metadata['version']}",
        "",
        str(metadata["purpose"]),
        "",
        f"**Source:** `{source_name}` (approved, source-controlled system knowledge)",
        "",
        str(metadata.get("usage_note") or ""),
        "",
    ]
    for section, values in document.items():
        if section in {"metadata", "parsing_guidance"} or not isinstance(values, dict):
            continue
        lines.extend([f"## {section.replace('_', ' ').title()}", ""])
        for key, value in values.items():
            if isinstance(value, dict):
                candidates = ", ".join(value.get("candidates") or [])
                rendered = f"{candidates}. {value.get('note') or ''}".strip()
            else:
                rendered = str(value)
            lines.append(f"- `{key}` → {rendered}")
        lines.append("")
    lines.extend(["## Parsing guidance", ""])
    lines.extend(f"- {value}" for value in document.get("parsing_guidance") or [])
    return "\n".join(lines).strip() + "\n"


def _seed_reference(*, tenant_id: str, path: Path, document_id: str,
                    version_id: str, version_seq: int, title: str,
                    renderer, metadata: dict[str, Any]) -> dict[str, Any]:
    import kb as kb_service

    source_bytes = path.read_bytes()
    # Historical packages remain displayable even when a later schema validator
    # intentionally rejects an ambiguity that motivated their replacement.  The
    # active packages are validated by ``resources()`` before runtime use.
    document = yaml.safe_load(source_bytes)
    if not isinstance(document, dict):
        raise ValueError(f"knowledge source must contain a YAML mapping: {path.name}")
    return kb_service.ensure_system_reference_document(
        tenant_id=tenant_id, document_id=document_id, version_id=version_id,
        version_seq=version_seq, title=title, source_filename=path.name,
        source_media_type="application/x-yaml", source_bytes=source_bytes,
        converted_markdown=renderer(document, path.name), metadata=metadata,
    )


def seed_documents(tenant_id: str = "bootstrap") -> dict[str, Any]:
    """Expose active and historical value-semantics and terminology versions."""
    values = [
        _seed_reference(
            tenant_id=tenant_id, path=VALUE_KB_V01_PATH,
            document_id=VALUE_DOCUMENT_ID, version_id=VALUE_VERSION_V01_ID,
            version_seq=1, title="Test 2, Diagnostic 8 — Value Semantics Knowledge Base",
            renderer=render_value_kb_markdown,
            metadata={"diagnostic_id": 8, "kb_version": "0.1", "lifecycle": "historical"},
        ),
        _seed_reference(
            tenant_id=tenant_id, path=VALUE_KB_PATH,
            document_id=VALUE_DOCUMENT_ID, version_id=VALUE_VERSION_ID,
            version_seq=2, title="Test 2, Diagnostic 8 — Value Semantics Knowledge Base",
            renderer=render_value_kb_markdown,
            metadata={"diagnostic_id": 8, "kb_version": "0.2", "lifecycle": "active"},
        ),
        _seed_reference(
            tenant_id=tenant_id, path=TERMINOLOGY_V02_PATH,
            document_id=TERMINOLOGY_DOCUMENT_ID,
            version_id=TERMINOLOGY_VERSION_V02_ID, version_seq=2,
            title="Credit Risk Abbreviation and Terminology Dictionary",
            renderer=render_terminology_markdown,
            metadata={"terminology_version": "0.2", "lifecycle": "historical"},
        ),
        _seed_reference(
            tenant_id=tenant_id, path=TERMINOLOGY_PATH,
            document_id=TERMINOLOGY_DOCUMENT_ID,
            version_id=TERMINOLOGY_VERSION_ID, version_seq=3,
            title="Credit Risk Abbreviation and Terminology Dictionary",
            renderer=render_terminology_markdown,
            metadata={"terminology_version": "0.3", "lifecycle": "active"},
        ),
    ]
    return {"documents": values, "inserted": any(value["inserted"] for value in values)}


__all__ = [
    "PROMPT_PATH", "TERMINOLOGY_PATH", "VALUE_KB_PATH", "prompt",
    "resources", "role_index", "seed_documents",
]
