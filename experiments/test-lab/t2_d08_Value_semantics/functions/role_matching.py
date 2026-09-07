"""Terminology-aware matching of physical variables to semantic roles."""
from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .kb_loader import TERMINOLOGY_MAPPING_SECTIONS


TOKEN_EXPANSION_SECTIONS = TERMINOLOGY_MAPPING_SECTIONS[:-1]
_CAMEL_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_CAMEL_WORD_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")
_LETTER_NUMBER_BOUNDARY = re.compile(r"(?<=[A-Za-z])(?=\d)")
_NON_ALPHANUMERIC = re.compile(r"[^\w]+", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
_COMPACT_DURATION = re.compile(r"^(\d+)([mqyw])$")
_DURATION_UNITS = {"m": ("month", "months"), "q": ("quarter", "quarters"), "y": ("year", "years"), "w": ("week", "weeks")}


def normalize_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = _CAMEL_ACRONYM_BOUNDARY.sub(" ", text)
    text = _CAMEL_WORD_BOUNDARY.sub(" ", text)
    text = _LETTER_NUMBER_BOUNDARY.sub(" ", text)
    text = re.sub(r"[_\-]+", " ", text)
    text = _NON_ALPHANUMERIC.sub(" ", text)
    return _WHITESPACE.sub(" ", text.lower()).strip()


def _normalized_mapping(values: Mapping[str, str]) -> dict[str, str]:
    return {normalize_text(key): normalize_text(value) for key, value in values.items()}


def _terminology_tables(terminology: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    compounds = _normalized_mapping(terminology["common_compound_terms"])
    ambiguous = {normalize_text(key): value for key, value in terminology["ambiguous_abbreviations"].items()}
    tokens: dict[str, str] = {}
    for section in TOKEN_EXPANSION_SECTIONS:
        for key, value in _normalized_mapping(terminology[section]).items():
            if key not in ambiguous:
                tokens.setdefault(key, value)
    return compounds, tokens, ambiguous


def _replace_compounds(text: str, compounds: Mapping[str, str]) -> str:
    result = text
    for phrase, expansion in sorted(compounds.items(), key=lambda item: len(item[0].split()), reverse=True):
        result = re.sub(rf"(?<!\w){re.escape(phrase)}(?!\w)", expansion, result)
    return _WHITESPACE.sub(" ", result).strip()


def process_terminology(value: Any, terminology: Mapping[str, Any], *, tables=None) -> dict[str, Any]:
    normalized = normalize_text(value)
    compounds, tokens, ambiguous = tables or _terminology_tables(terminology)
    compound_expanded = _replace_compounds(normalized, compounds)
    expanded: list[str] = []
    unresolved: list[dict[str, Any]] = []
    for token in compound_expanded.split():
        if token in ambiguous:
            specification = ambiguous[token]
            unresolved.append({"abbreviation": token, "candidates": list(specification["candidates"]), "note": specification.get("note")})
            expanded.append(token)
            continue
        duration = _COMPACT_DURATION.fullmatch(token)
        if duration:
            count, unit = duration.groups()
            singular, plural = _DURATION_UNITS[unit]
            expanded.append(f"{count} {singular if count == '1' else plural}")
        else:
            expanded.append(tokens.get(token, token))
    return {"normalized_text": normalized, "expanded_text": " ".join(expanded), "unresolved_ambiguities": unresolved}


@dataclass(frozen=True)
class PreparedRoleMatcher:
    kb: Mapping[str, Any]
    terminology: Mapping[str, Any]
    terminology_tables: tuple[dict[str, str], dict[str, str], dict[str, Any]]
    role_pieces: tuple[tuple[dict[str, str], ...], ...]
    documents: tuple[str, ...]
    exact_index: Mapping[str, tuple[dict[str, str], ...]]
    full_catalog: tuple[dict[str, Any], ...]


def prepare_role_matcher(kb: Mapping[str, Any], terminology: Mapping[str, Any]) -> PreparedRoleMatcher:
    tables = _terminology_tables(terminology)
    all_pieces: list[tuple[dict[str, str], ...]] = []
    exact: dict[str, list[dict[str, str]]] = {}
    catalog: list[dict[str, Any]] = []
    for role in kb["semantic_roles"]:
        pieces: list[dict[str, str]] = []
        for source, values in (
            ("canonical", [role["role"]]),
            ("definition", [role["definition"]]),
            ("representation", role["representations"]),
            ("matching_note", [role["matching_note"]] if role.get("matching_note") else []),
        ):
            for value in values:
                processed = process_terminology(value, terminology, tables=tables)["expanded_text"]
                piece = {"source": source, "original": str(value), "processed": processed}
                pieces.append(piece)
                if source in {"canonical", "representation"} and processed:
                    exact.setdefault(processed, []).append({"role": role["role"], "match_source": source, "matched_text": str(value)})
        all_pieces.append(tuple(pieces))
        catalog.append({"role": role["role"], "definition": role["definition"], "role_kind": role["role_kind"], "model_types": list(role.get("model_types", []))})
    return PreparedRoleMatcher(
        kb=kb,
        terminology=terminology,
        terminology_tables=tables,
        role_pieces=tuple(all_pieces),
        documents=tuple(" ".join(dict.fromkeys(piece["processed"] for piece in pieces if piece["processed"])) for pieces in all_pieces),
        exact_index={key: tuple(values) for key, values in exact.items()},
        full_catalog=tuple(catalog),
    )


def _rank(query: str, prepared: PreparedRoleMatcher) -> list[dict[str, Any]]:
    documents = list(prepared.documents)
    vectorizer = TfidfVectorizer(lowercase=False, ngram_range=(1, 2), stop_words="english", sublinear_tf=True)
    matrix = vectorizer.fit_transform([query, *documents])
    tfidf = cosine_similarity(matrix[0:1], matrix[1:]).ravel()
    ranked = []
    for role, pieces, document, tfidf_score in zip(prepared.kb["semantic_roles"], prepared.role_pieces, documents, tfidf, strict=True):
        fuzzy = fuzz.token_set_ratio(query, document) / 100.0
        best_score, best_piece = max(
            ((fuzz.token_set_ratio(query, piece["processed"]) / 100.0, piece) for piece in pieces),
            key=lambda item: item[0],
        )
        ranked.append({
            "role": role["role"],
            "definition": role["definition"],
            "representations": list(role["representations"]),
            "matching_note": role.get("matching_note"),
            "combined_score": round(0.5 * fuzzy + 0.5 * float(tfidf_score), 6),
            "tfidf_score": round(float(tfidf_score), 6),
            "best_matching_text": best_piece["original"],
            "best_matching_source": best_piece["source"],
            "best_text_score": round(best_score, 6),
        })
    ranked.sort(key=lambda item: (-item["combined_score"], -item["tfidf_score"], item["role"]))
    for index, item in enumerate(ranked, 1):
        item["rank"] = index
    return ranked


def match_variable_to_roles(
    variable: Mapping[str, Any],
    prepared: PreparedRoleMatcher,
    *,
    include_context_hints: bool = True,
) -> dict[str, Any]:
    name = str(variable.get("name") or variable.get("column_name") or "").strip()
    if not name:
        raise ValueError("Variable name is required")
    description = str(variable.get("description") or "")
    business_name = str(variable.get("business_name") or "")
    allowed_values = str(variable.get("allowed_values") or "")
    model_type = str(variable.get("model_type") or variable.get("suggested_context") or "")
    use_cases = variable.get("use_cases") or []
    if isinstance(use_cases, str):
        use_cases = [value.strip() for value in use_cases.split(";") if value.strip()]
    product = str(variable.get("product") or "")
    allowed_roles = prepared.kb["matching_contract"]["input_fields"]["role"]["allowed_values"]
    default_role = "ignore" if "ignore" in allowed_roles else allowed_roles[-1]
    production_role = str(variable.get("role") or default_role).strip().lower()
    if production_role not in allowed_roles:
        raise ValueError(f"Unsupported production role {production_role!r}")
    processed_name = process_terminology(name, prepared.terminology, tables=prepared.terminology_tables)
    processed_description = process_terminology(description, prepared.terminology, tables=prepared.terminology_tables)
    processed_business_name = process_terminology(business_name, prepared.terminology, tables=prepared.terminology_tables)
    unresolved = [*processed_name["unresolved_ambiguities"], *processed_description["unresolved_ambiguities"], *processed_business_name["unresolved_ambiguities"]]
    ambiguity_text = " ".join(candidate for item in unresolved for candidate in item["candidates"])
    context_text = " ".join([model_type, *use_cases, product]) if include_context_hints else ""
    query = " ".join(part for part in (processed_name["expanded_text"], processed_business_name["expanded_text"], processed_description["expanded_text"], allowed_values, production_role, context_text, ambiguity_text) if part)
    exact_matches = list(prepared.exact_index.get(processed_name["expanded_text"], ()))
    exact_roles = sorted({item["role"] for item in exact_matches})
    base = {
        "input_variable": name,
        "description": description,
        "business_name": business_name,
        "allowed_values": allowed_values,
        "model_type": model_type,
        "use_cases": list(use_cases),
        "product": product,
        "data_type": variable.get("data_type"),
        "production_role": production_role,
        "expanded_name": processed_name["expanded_text"],
        "expanded_description": processed_description["expanded_text"],
        "unresolved_abbreviations": unresolved,
        "full_catalog": list(prepared.full_catalog),
    }
    if len(exact_roles) == 1:
        selected = next(item for item in exact_matches if item["role"] == exact_roles[0])
        return {**base, "match_status": "exact_match", "match_method": "deterministic_exact", "exact_match": selected, "detailed_candidates": []}

    ranked = _rank(query, prepared)
    contract = prepared.kb["matching_contract"]["candidate_generation"]["detailed_candidate_subset"]
    minimum = int(contract["minimum_candidates"])
    soft_maximum = int(contract["soft_maximum_candidates"])
    selected_count = min(max(minimum, min(soft_maximum, len(ranked))), len(ranked))
    selected = ranked[:selected_count]
    if len(ranked) > selected_count and selected:
        boundary = selected[-1]["combined_score"]
        margin = float(contract.get("score_margin", 0.05))
        evidence_floor = float(contract.get("minimum_boundary_score_for_margin_expansion", 0.0))
        if boundary >= evidence_floor:
            selected.extend(item for item in ranked[selected_count:] if item["combined_score"] > 0 and boundary - item["combined_score"] <= margin)
    if exact_roles:
        selected_roles = {item["role"] for item in selected}
        missing_collision_roles = set(exact_roles) - selected_roles
        selected.extend(item for item in ranked if item["role"] in missing_collision_roles)
        selected.sort(key=lambda item: item["rank"])
    has_evidence = any(item["tfidf_score"] > 0 for item in ranked)
    return {
        **base,
        "match_status": "alias_collision" if exact_roles else ("candidate_match" if has_evidence else "no_lexical_evidence"),
        "match_method": "adaptive_rapidfuzz_tfidf",
        "exact_match": None,
        "exact_collision_roles": exact_roles,
        "detailed_candidates": selected,
    }
