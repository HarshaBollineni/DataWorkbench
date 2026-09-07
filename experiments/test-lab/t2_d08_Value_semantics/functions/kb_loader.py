"""Load and validate value-semantics YAML resources."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


TERMINOLOGY_MAPPING_SECTIONS = (
    "domain_acronyms",
    "standard_abbreviations",
    "time_abbreviations",
    "aggregation_abbreviations",
    "transformation_abbreviations",
    "consumer_credit_terms",
    "mortgage_terms",
    "commercial_credit_terms",
    "cre_terms",
    "macroeconomic_terms",
    "common_compound_terms",
)


def _read_yaml(path: str | Path, resource_name: str) -> dict[str, Any]:
    resource_path = Path(path)
    if not resource_path.is_file():
        raise FileNotFoundError(f"{resource_name} YAML not found: {resource_path}")
    try:
        document = yaml.safe_load(resource_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed {resource_name} YAML at {resource_path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{resource_name} YAML root must be a mapping")
    return document


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        raise ValueError(f"Duplicate {label}: {duplicates}")


def load_value_semantics_kb(path: str | Path) -> dict[str, Any]:
    kb = _read_yaml(path, "value-semantics Knowledge Base")
    for key in ("metadata", "conventions", "matching_contract", "semantic_roles", "rules"):
        if key not in kb:
            raise ValueError(f"Value-semantics Knowledge Base requires {key!r}")

    roles = kb["semantic_roles"]
    if not isinstance(roles, list) or not roles:
        raise ValueError("semantic_roles must be a non-empty list")
    role_ids: list[str] = []
    for index, role in enumerate(roles):
        if not isinstance(role, Mapping):
            raise ValueError(f"semantic_roles[{index}] must be a mapping")
        for field in ("role", "definition", "representations", "binding_cardinality", "role_kind"):
            if field not in role:
                raise ValueError(f"semantic_roles[{index}] requires {field!r}")
        if not isinstance(role["representations"], list) or not role["representations"]:
            raise ValueError(f"semantic_roles[{index}].representations must be non-empty")
        role_ids.append(str(role["role"]))
    _unique(role_ids, "semantic role identifiers")
    role_set = set(role_ids)
    implications = {str(role["role"]): [str(value) for value in role.get("implied_roles", [])] for role in roles}
    for role_id, implied in implications.items():
        unknown = set(implied) - role_set
        if unknown or role_id in implied:
            raise ValueError(f"Invalid implied_roles for {role_id!r}: {sorted(unknown | ({role_id} & set(implied)))}")

    def visit(role_id: str, path: set[str]) -> None:
        if role_id in path:
            raise ValueError(f"Cyclic implied_roles involving {role_id!r}")
        for implied_role in implications[role_id]:
            visit(implied_role, path | {role_id})
    for role_id in role_ids:
        visit(role_id, set())

    rules = kb["rules"]
    if not isinstance(rules, list) or not rules:
        raise ValueError("rules must be a non-empty list")
    rule_ids = [str(rule.get("rule")) for rule in rules]
    _unique(rule_ids, "rule identifiers")
    entry_ids: list[str] = []
    referenced_roles: set[str] = set()
    for rule in rules:
        entries = rule.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"Rule {rule.get('rule')!r} requires entries")
        for entry in entries:
            entry_ids.append(str(entry.get("entry")))
            referenced_roles.add(str(entry.get("target_role")))
            referenced_roles.update(str(value) for value in entry.get("indicator_roles", []))
    _unique(entry_ids, "rule entry identifiers")
    missing = sorted(referenced_roles - set(role_ids))
    if missing:
        raise ValueError(f"Rule entries reference unknown semantic roles: {missing}")

    candidate_contract = kb["matching_contract"].get("candidate_generation", {})
    if candidate_contract.get("policy") != "adaptive_high_recall":
        raise ValueError("matching_contract must use adaptive_high_recall candidate generation")
    if candidate_contract.get("detailed_candidate_subset", {}).get("hard_maximum_candidates") is not None:
        raise ValueError("hard_maximum_candidates must be null")
    return kb


def load_terminology(path: str | Path) -> dict[str, Any]:
    terminology = _read_yaml(path, "credit-risk terminology")
    for section in TERMINOLOGY_MAPPING_SECTIONS:
        values = terminology.get(section)
        if not isinstance(values, dict):
            raise ValueError(f"credit-risk terminology requires mapping section {section!r}")
        if any(not str(key).strip() or not isinstance(value, str) or not value.strip() for key, value in values.items()):
            raise ValueError(f"credit-risk terminology section {section!r} contains an invalid mapping")
    ambiguous = terminology.get("ambiguous_abbreviations")
    if not isinstance(ambiguous, dict):
        raise ValueError("credit-risk terminology requires ambiguous_abbreviations")
    for abbreviation, specification in ambiguous.items():
        if not isinstance(specification, dict) or len(specification.get("candidates", [])) < 2:
            raise ValueError(f"ambiguous_abbreviations.{abbreviation} requires at least two candidates")
    ambiguous_keys = {str(key).strip().lower() for key in ambiguous}
    expansions: dict[str, set[str]] = {}
    for section in TERMINOLOGY_MAPPING_SECTIONS:
        for key, value in terminology[section].items():
            expansions.setdefault(str(key).strip().lower(), set()).add(str(value).strip().lower())
    conflicts = sorted(key for key, values in expansions.items() if len(values) > 1 and key not in ambiguous_keys)
    if conflicts:
        raise ValueError(f"Conflicting terminology expansions must be declared ambiguous: {conflicts}")
    return terminology


def expand_implied_roles(roles: list[str] | set[str] | tuple[str, ...], kb: Mapping[str, Any]) -> list[str]:
    """Return direct roles plus their transitive deterministic implications."""
    implications = {role["role"]: role.get("implied_roles", []) for role in kb["semantic_roles"]}
    expanded = list(dict.fromkeys(roles))
    index = 0
    while index < len(expanded):
        for implied in implications.get(expanded[index], []):
            if implied not in expanded:
                expanded.append(implied)
        index += 1
    return expanded
