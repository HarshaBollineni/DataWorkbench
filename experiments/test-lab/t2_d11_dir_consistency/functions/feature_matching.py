"""Terminology-aware matching of dataset features to the PD directionality KB.

This module intentionally stops at deterministic matching and auditable NLP
candidate ranking.  It does not orient targets, calculate directionality,
accept an NLP candidate, or call an LLM.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


KB_REQUIRED_FIELDS = (
    "feature",
    "feature_family",
    "definition",
    "representations",
    "risk_outcome",
    "expected_direction",
    "knowledge_strength",
    "rationale",
)
KB_OPTIONAL_FIELDS = ("inverse_representations", "transformation", "notes")

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
TOKEN_EXPANSION_SECTIONS = TERMINOLOGY_MAPPING_SECTIONS[:-1]

DEFAULT_RAPIDFUZZ_WEIGHT = 0.5
DEFAULT_TFIDF_WEIGHT = 0.5

_CAMEL_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_CAMEL_WORD_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")
_LETTER_NUMBER_BOUNDARY = re.compile(r"(?<=[A-Za-z])(?=\d)")
_NON_ALPHANUMERIC = re.compile(r"[^\w]+", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
_COMPACT_DURATION = re.compile(r"^(\d+)([mqyw])$")
_DURATION_UNITS = {
    "m": ("month", "months"),
    "q": ("quarter", "quarters"),
    "y": ("year", "years"),
    "w": ("week", "weeks"),
}


def _read_yaml(path: str | Path, resource_name: str) -> dict[str, Any]:
    resource_path = Path(path)
    if not resource_path.is_file():
        raise FileNotFoundError(f"{resource_name} YAML not found: {resource_path}")
    try:
        with resource_path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed {resource_name} YAML at {resource_path}: {exc}") from exc
    except UnicodeError as exc:
        raise ValueError(f"{resource_name} YAML is not valid UTF-8: {resource_path}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{resource_name} YAML root must be a mapping: {resource_path}")
    return document


def _require_nonempty_string(value: Any, location: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be a non-empty string")


def _require_string_list(value: Any, location: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise ValueError(f"{location} must be {qualifier} of strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{location} must contain only non-empty strings")


def load_kb(path: str | Path) -> dict[str, Any]:
    """Load and lightly validate the PD directionality Knowledge Base YAML."""
    kb = _read_yaml(path, "PD directionality Knowledge Base")
    rules = kb.get("feature_rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("PD directionality Knowledge Base requires a non-empty 'feature_rules' list")

    seen_features: set[str] = set()
    for index, rule in enumerate(rules):
        location = f"feature_rules[{index}]"
        if not isinstance(rule, dict):
            raise ValueError(f"{location} must be a mapping")
        missing = [field for field in KB_REQUIRED_FIELDS if field not in rule]
        if missing:
            raise ValueError(f"{location} is missing required field(s): {', '.join(missing)}")
        for field in KB_REQUIRED_FIELDS:
            if field == "representations":
                _require_string_list(rule[field], f"{location}.representations")
            else:
                _require_nonempty_string(rule[field], f"{location}.{field}")
        feature = rule["feature"]
        if feature in seen_features:
            raise ValueError(f"Duplicate canonical feature in feature_rules: {feature!r}")
        seen_features.add(feature)

        if "inverse_representations" in rule:
            _require_string_list(
                rule["inverse_representations"],
                f"{location}.inverse_representations",
            )
        for field in ("transformation", "notes"):
            if field in rule and rule[field] is not None:
                _require_nonempty_string(rule[field], f"{location}.{field}")
    return kb


def load_terminology(path: str | Path) -> dict[str, Any]:
    """Load terminology sections while keeping ambiguous terms separate."""
    terminology = _read_yaml(path, "credit-risk terminology")
    for section in TERMINOLOGY_MAPPING_SECTIONS:
        values = terminology.get(section)
        if not isinstance(values, dict):
            raise ValueError(f"credit-risk terminology requires mapping section {section!r}")
        for key, value in values.items():
            _require_nonempty_string(key, f"{section} key")
            _require_nonempty_string(value, f"{section}.{key}")

    ambiguous = terminology.get("ambiguous_abbreviations")
    if not isinstance(ambiguous, dict):
        raise ValueError("credit-risk terminology requires 'ambiguous_abbreviations' mapping")
    for abbreviation, specification in ambiguous.items():
        location = f"ambiguous_abbreviations.{abbreviation}"
        _require_nonempty_string(abbreviation, f"{location} key")
        if not isinstance(specification, dict):
            raise ValueError(f"{location} must be a mapping")
        _require_string_list(specification.get("candidates"), f"{location}.candidates")
        if len(specification["candidates"]) < 2:
            raise ValueError(f"{location}.candidates must contain at least two meanings")
        if "note" in specification:
            _require_nonempty_string(specification["note"], f"{location}.note")

    guidance = terminology.get("parsing_guidance")
    _require_string_list(guidance, "parsing_guidance")
    return terminology


def normalize_text(value: Any) -> str:
    """Normalize feature text without discarding meaningful numeric tokens."""
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


def _replace_compounds(text: str, compounds: Mapping[str, str]) -> str:
    result = text
    ordered = sorted(compounds.items(), key=lambda pair: len(pair[0].split()), reverse=True)
    for phrase, expansion in ordered:
        if not phrase:
            continue
        result = re.sub(
            rf"(?<!\w){re.escape(phrase)}(?!\w)",
            expansion,
            result,
        )
    return _WHITESPACE.sub(" ", result).strip()


def _duration_expansion(token: str) -> str | None:
    match = _COMPACT_DURATION.fullmatch(token)
    if not match:
        return None
    count, unit = match.groups()
    singular, plural = _DURATION_UNITS[unit]
    return f"{count} {singular if count == '1' else plural}"


def _terminology_tables(
    terminology: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    compounds = _normalized_mapping(terminology["common_compound_terms"])
    ambiguous = {normalize_text(k): v for k, v in terminology["ambiguous_abbreviations"].items()}

    # Section order is the YAML parsing-guidance order. Earlier deterministic
    # sections win when the same token appears in multiple domain vocabularies.
    token_expansions: dict[str, str] = {}
    for section in TOKEN_EXPANSION_SECTIONS:
        for key, value in _normalized_mapping(terminology[section]).items():
            if key not in ambiguous:
                token_expansions.setdefault(key, value)
    return compounds, token_expansions, ambiguous


def _contextual_ambiguity_evidence(
    candidates: list[str], context_text: str
) -> list[dict[str, Any]]:
    context_tokens = set(normalize_text(context_text).split())
    evidence = []
    for candidate in candidates:
        candidate_tokens = set(normalize_text(candidate).split()) - {"a", "an", "of", "the", "to"}
        overlap = sorted(candidate_tokens & context_tokens)
        score = len(overlap) / len(candidate_tokens) if candidate_tokens else 0.0
        evidence.append(
            {
                "candidate": candidate,
                "context_token_overlap": overlap,
                "context_overlap_score": round(score, 6),
            }
        )
    return evidence


def _expand_terminology(
    value: Any,
    terminology: Mapping[str, Any],
    *,
    context_text: str = "",
    terminology_tables: tuple[dict[str, str], dict[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = normalize_text(value)
    compounds, token_expansions, ambiguous = (
        terminology_tables or _terminology_tables(terminology)
    )
    compound_expanded = _replace_compounds(normalized, compounds)

    expanded_parts: list[str] = []
    unresolved: list[dict[str, Any]] = []
    for token in compound_expanded.split():
        if token in ambiguous:
            specification = ambiguous[token]
            candidates = list(specification["candidates"])
            unresolved.append(
                {
                    "abbreviation": token,
                    "candidates": candidates,
                    "note": specification.get("note"),
                    "context_evidence": _contextual_ambiguity_evidence(candidates, context_text),
                }
            )
            expanded_parts.append(token)
            continue
        duration = _duration_expansion(token)
        expanded_parts.append(duration or token_expansions.get(token, token))

    return {
        "normalized_text": normalized,
        "expanded_text": _WHITESPACE.sub(" ", " ".join(expanded_parts)).strip(),
        "unresolved_ambiguities": unresolved,
    }


def process_terminology(
    value: Any,
    terminology: Mapping[str, Any],
    *,
    context_text: str = "",
) -> dict[str, Any]:
    """Normalize and expand one text value using only the supplied YAML."""
    return _expand_terminology(value, terminology, context_text=context_text)


def _rule_text_pieces(
    rule: Mapping[str, Any],
    terminology: Mapping[str, Any],
    *,
    terminology_tables: tuple[dict[str, str], dict[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    pieces = [
        {
            "source": "canonical",
            "original": str(rule["feature"]),
            "processed": _expand_terminology(
                rule["feature"], terminology, terminology_tables=terminology_tables
            )["expanded_text"],
        },
        {
            "source": "definition",
            "original": str(rule["definition"]),
            "processed": _expand_terminology(
                rule["definition"], terminology, terminology_tables=terminology_tables
            )["expanded_text"],
        },
    ]
    for source, field in (
        ("representation", "representations"),
        ("inverse_representation", "inverse_representations"),
    ):
        for value in rule.get(field, []):
            pieces.append(
                {
                    "source": source,
                    "original": str(value),
                    "processed": _expand_terminology(
                        value, terminology, terminology_tables=terminology_tables
                    )["expanded_text"],
                }
            )
    return pieces


def _exact_index(
    kb: Mapping[str, Any], terminology: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    terminology_tables = _terminology_tables(terminology)
    rule_pieces = [
        _rule_text_pieces(
            rule,
            terminology,
            terminology_tables=terminology_tables,
        )
        for rule in kb["feature_rules"]
    ]
    return _exact_index_from_pieces(kb["feature_rules"], rule_pieces)


def _exact_index_from_pieces(
    rules: list[Mapping[str, Any]],
    rule_pieces: list[list[dict[str, str]]],
) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for rule, pieces in zip(rules, rule_pieces, strict=True):
        for piece in pieces:
            if piece["source"] == "definition" or not piece["processed"]:
                continue
            index.setdefault(piece["processed"], []).append(
                {
                    "canonical_feature": rule["feature"],
                    "feature_family": rule["feature_family"],
                    "match_source": piece["source"],
                    "matched_kb_text": piece["original"],
                    "is_inverse_representation": piece["source"] == "inverse_representation",
                    "expected_direction": rule["expected_direction"],
                    "knowledge_strength": rule["knowledge_strength"],
                }
            )
    return index


def _matching_document(pieces: list[dict[str, str]]) -> str:
    # Rationale is deliberately absent: only canonical, definition, normal
    # representations and inverse representations are supplied as pieces.
    return " ".join(dict.fromkeys(piece["processed"] for piece in pieces if piece["processed"]))


@dataclass(frozen=True)
class PreparedFeatureMatcher:
    """Immutable-by-convention preprocessing shared by feature-match calls."""

    kb: Mapping[str, Any]
    terminology: Mapping[str, Any]
    terminology_tables: tuple[dict[str, str], dict[str, str], dict[str, Any]]
    rule_pieces: list[list[dict[str, str]]]
    exact_index: dict[str, list[dict[str, Any]]]
    matching_documents: list[str]


def prepare_feature_matcher(
    kb: Mapping[str, Any], terminology: Mapping[str, Any]
) -> PreparedFeatureMatcher:
    """Prepare unchanged terminology and KB matching artifacts once."""
    terminology_tables = _terminology_tables(terminology)
    rules = kb["feature_rules"]
    rule_pieces = [
        _rule_text_pieces(
            rule,
            terminology,
            terminology_tables=terminology_tables,
        )
        for rule in rules
    ]
    return PreparedFeatureMatcher(
        kb=kb,
        terminology=terminology,
        terminology_tables=terminology_tables,
        rule_pieces=rule_pieces,
        exact_index=_exact_index_from_pieces(rules, rule_pieces),
        matching_documents=[_matching_document(pieces) for pieces in rule_pieces],
    )


def _rank_candidates(
    query: str,
    prepared_matcher: PreparedFeatureMatcher,
    *,
    rapidfuzz_weight: float,
    tfidf_weight: float,
) -> list[dict[str, Any]]:
    rules = prepared_matcher.kb["feature_rules"]
    rule_pieces = prepared_matcher.rule_pieces
    documents = prepared_matcher.matching_documents

    vectorizer = TfidfVectorizer(
        lowercase=False,
        ngram_range=(1, 2),
        stop_words="english",
        sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform([query, *documents])
    tfidf_scores = cosine_similarity(matrix[0:1], matrix[1:]).ravel()

    candidates: list[dict[str, Any]] = []
    for rule, pieces, document, tfidf_score in zip(
        rules, rule_pieces, documents, tfidf_scores, strict=True
    ):
        rapidfuzz_score = fuzz.token_set_ratio(query, document) / 100.0
        piece_scores = [
            (fuzz.token_set_ratio(query, piece["processed"]) / 100.0, piece)
            for piece in pieces
        ]
        best_piece_score, best_piece = max(piece_scores, key=lambda item: item[0])
        combined = rapidfuzz_weight * rapidfuzz_score + tfidf_weight * float(tfidf_score)
        source = best_piece["source"]
        candidates.append(
            {
                "canonical_feature": rule["feature"],
                "feature_family": rule["feature_family"],
                "combined_similarity_score": round(combined, 6),
                "rapidfuzz_score": round(rapidfuzz_score, 6),
                "tfidf_cosine_score": round(float(tfidf_score), 6),
                "expected_direction": rule["expected_direction"],
                "knowledge_strength": rule["knowledge_strength"],
                "best_matching_kb_text": best_piece["original"],
                "best_matching_kb_text_source": source,
                "best_text_rapidfuzz_score": round(best_piece_score, 6),
                "representation_evidence": (
                    "inverse" if source == "inverse_representation" else "normal"
                ),
            }
        )
    candidates.sort(
        key=lambda candidate: (
            -candidate["combined_similarity_score"],
            -candidate["tfidf_cosine_score"],
            candidate["canonical_feature"],
        )
    )
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
    return candidates


def _validate_weights(rapidfuzz_weight: float, tfidf_weight: float) -> None:
    if rapidfuzz_weight < 0 or tfidf_weight < 0:
        raise ValueError("Similarity weights must be non-negative")
    if not math.isclose(rapidfuzz_weight + tfidf_weight, 1.0, abs_tol=1e-9):
        raise ValueError("RapidFuzz and TF-IDF weights must sum to 1.0")


def match_feature_to_kb(
    feature_name: str,
    feature_description: str | None,
    kb: Mapping[str, Any],
    terminology: Mapping[str, Any],
    top_n: int = 3,
    *,
    rapidfuzz_weight: float = DEFAULT_RAPIDFUZZ_WEIGHT,
    tfidf_weight: float = DEFAULT_TFIDF_WEIGHT,
    prepared_matcher: PreparedFeatureMatcher | None = None,
) -> dict[str, Any]:
    """Return an exact match or an unaccepted, ranked candidate set."""
    if not isinstance(feature_name, str) or not feature_name.strip():
        raise ValueError("feature_name must be a non-empty string")
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n <= 0:
        raise ValueError("top_n must be a positive integer")
    _validate_weights(rapidfuzz_weight, tfidf_weight)
    prepared = prepared_matcher or prepare_feature_matcher(kb, terminology)
    if prepared.kb is not kb or prepared.terminology is not terminology:
        raise ValueError("prepared_matcher must have been built from the supplied kb and terminology")

    description = (
        ""
        if feature_description is None
        or (isinstance(feature_description, float) and math.isnan(feature_description))
        else str(feature_description)
    )
    processed_name = _expand_terminology(
        feature_name,
        terminology,
        context_text=description,
        terminology_tables=prepared.terminology_tables,
    )
    processed_description = _expand_terminology(
        description,
        terminology,
        terminology_tables=prepared.terminology_tables,
    )
    unresolved = [
        *processed_name["unresolved_ambiguities"],
        *processed_description["unresolved_ambiguities"],
    ]
    base_result: dict[str, Any] = {
        "original_feature_name": feature_name,
        "original_description": description,
        "normalized_feature_name": processed_name["normalized_text"],
        "expanded_feature_name": processed_name["expanded_text"],
        "expanded_description": processed_description["expanded_text"],
        "processed_feature_text": " ".join(
            part
            for part in (
                processed_name["expanded_text"],
                processed_description["expanded_text"],
            )
            if part
        ),
        "unresolved_ambiguous_terminology": unresolved,
        "similarity_weights": {
            "rapidfuzz": rapidfuzz_weight,
            "tfidf": tfidf_weight,
        },
    }

    exact_matches = prepared.exact_index.get(processed_name["expanded_text"], [])
    if exact_matches:
        # A duplicate normalized alias is not silently adjudicated. The v0.2
        # resources have no such cross-concept collision, but keep all evidence.
        canonical_features = {match["canonical_feature"] for match in exact_matches}
        if len(canonical_features) == 1:
            selected = exact_matches[0]
            return {
                **base_result,
                "match_status": "exact_match",
                "match_method": "deterministic_exact",
                "deterministic_match": selected,
                "deterministic_match_alternatives": exact_matches[1:],
                "top_candidates": [],
                "top_1_score": 1.0,
                "top_2_score": None,
                "top_1_minus_top_2_score_gap": None,
            }

    ambiguity_candidate_text = " ".join(
        candidate
        for item in unresolved
        for candidate in item["candidates"]
    )
    query = " ".join(
        part for part in (base_result["processed_feature_text"], ambiguity_candidate_text) if part
    )
    ranked = _rank_candidates(
        query,
        prepared,
        rapidfuzz_weight=rapidfuzz_weight,
        tfidf_weight=tfidf_weight,
    )
    selected_candidates = ranked[: min(top_n, len(ranked))]
    top_1 = ranked[0]["combined_similarity_score"] if ranked else None
    top_2 = ranked[1]["combined_similarity_score"] if len(ranked) > 1 else None
    gap = round(top_1 - top_2, 6) if top_1 is not None and top_2 is not None else None

    # This is an evidence-presence check, not an acceptance threshold. With no
    # shared TF-IDF vocabulary, fuzzy edit similarity alone does not create a
    # semantic candidate match (for example RANDOM_VAR_X).
    has_lexical_evidence = any(candidate["tfidf_cosine_score"] > 0 for candidate in ranked)
    return {
        **base_result,
        "matching_text": query,
        "match_status": "candidate_match" if has_lexical_evidence else "no_match",
        "match_method": "rapidfuzz_tfidf" if has_lexical_evidence else "no_lexical_evidence",
        "deterministic_match": None,
        "deterministic_match_alternatives": exact_matches,
        "top_candidates": selected_candidates,
        "top_1_score": top_1,
        "top_2_score": top_2,
        "top_1_minus_top_2_score_gap": gap,
    }


def _metadata_records(feature_metadata: Any) -> list[Mapping[str, Any]]:
    if isinstance(feature_metadata, pd.DataFrame):
        return feature_metadata.to_dict(orient="records")
    if isinstance(feature_metadata, Mapping):
        return [feature_metadata]
    if isinstance(feature_metadata, Iterable) and not isinstance(feature_metadata, (str, bytes)):
        records = list(feature_metadata)
        if not all(isinstance(record, Mapping) for record in records):
            raise TypeError("feature_metadata iterable must contain mappings")
        return records
    raise TypeError("feature_metadata must be a DataFrame, mapping, or iterable of mappings")


def _record_value(record: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in record:
            return record[name]
    return None


def match_features_to_kb(
    feature_metadata: Any,
    kb: Mapping[str, Any],
    terminology: Mapping[str, Any],
    top_n: int = 3,
    *,
    include_details: bool = False,
    rapidfuzz_weight: float = DEFAULT_RAPIDFUZZ_WEIGHT,
    tfidf_weight: float = DEFAULT_TFIDF_WEIGHT,
) -> pd.DataFrame | tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Match a batch and return a notebook-friendly one-row-per-feature table.

    Accepted name columns are ``feature_name``, ``feature``, ``name`` and
    ``column_name``. Descriptions may use ``feature_description`` or
    ``description``. Set ``include_details=True`` to also receive every full
    structured result.
    """
    prepared_matcher = prepare_feature_matcher(kb, terminology)
    details = []
    summary_rows = []
    for record in _metadata_records(feature_metadata):
        name = _record_value(record, ("feature_name", "feature", "name", "column_name"))
        description = _record_value(record, ("feature_description", "description"))
        result = match_feature_to_kb(
            name,
            description,
            kb,
            terminology,
            top_n=top_n,
            rapidfuzz_weight=rapidfuzz_weight,
            tfidf_weight=tfidf_weight,
            prepared_matcher=prepared_matcher,
        )
        details.append(result)
        deterministic = result["deterministic_match"]
        candidates = result["top_candidates"]
        first = candidates[0] if candidates else None
        second = candidates[1] if len(candidates) > 1 else None
        summary_rows.append(
            {
                "input_feature": result["original_feature_name"],
                "processed_feature_text": result["processed_feature_text"],
                "match_status": result["match_status"],
                "top_candidate": (
                    deterministic["canonical_feature"] if deterministic else
                    first["canonical_feature"] if first else None
                ),
                "combined_score": 1.0 if deterministic else (
                    first["combined_similarity_score"] if first else None
                ),
                "second_candidate": second["canonical_feature"] if second else None,
                "second_score": second["combined_similarity_score"] if second else None,
                "score_gap": result["top_1_minus_top_2_score_gap"],
                "match_source_type": deterministic["match_source"] if deterministic else (
                    first["best_matching_kb_text_source"] if first else None
                ),
                "unresolved_ambiguities": [
                    item["abbreviation"]
                    for item in result["unresolved_ambiguous_terminology"]
                ],
                "candidates": candidates,
            }
        )
    frame = pd.DataFrame(summary_rows)
    return (frame, details) if include_details else frame


__all__ = [
    "PreparedFeatureMatcher",
    "load_kb",
    "load_terminology",
    "match_feature_to_kb",
    "match_features_to_kb",
    "normalize_text",
    "prepare_feature_matcher",
    "process_terminology",
]
