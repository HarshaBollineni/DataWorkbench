"""Sentence-embedding challenger for PD Knowledge Base feature matching.

The baseline RapidFuzz/TF-IDF matcher is not used for semantic ranking. This
module reuses only its deterministic preprocessing and exact-match artifacts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import numpy as np
import pandas as pd

from .feature_matching import (
    PreparedFeatureMatcher,
    _expand_terminology,
    prepare_feature_matcher,
)


DEFAULT_SEMANTIC_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


@dataclass(frozen=True)
class SemanticFeatureIndex:
    """Precomputed canonical KB documents and normalized embeddings."""

    model_name: str
    package_version: str
    model: Any
    kb: dict[str, Any]
    terminology: dict[str, Any]
    deterministic_index: PreparedFeatureMatcher
    canonical_features: tuple[str, ...]
    semantic_documents: tuple[str, ...]
    embeddings: np.ndarray
    embedding_dimension: int


def load_sentence_transformer(
    model_name: str = DEFAULT_SEMANTIC_MODEL,
    *,
    local_files_only: bool = True,
) -> Any:
    """Load a SentenceTransformer from the local model cache by default."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, local_files_only=local_files_only)


def _semantic_document(pieces: list[dict[str, str]]) -> str:
    return ". ".join(
        dict.fromkeys(piece["processed"] for piece in pieces if piece["processed"])
    )


def _encode_normalized(model: Any, texts: list[str] | tuple[str, ...]) -> np.ndarray:
    embeddings = model.encode(
        list(texts),
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    matrix = np.asarray(embeddings, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2 or matrix.shape[0] != len(texts):
        raise ValueError(
            f"Sentence transformer returned shape {matrix.shape}; expected {len(texts)} rows"
        )
    return matrix


def build_semantic_index(
    kb: dict[str, Any],
    terminology: dict[str, Any],
    *,
    model: Any | None = None,
    model_name: str = DEFAULT_SEMANTIC_MODEL,
) -> SemanticFeatureIndex:
    """Encode the 39 canonical KB documents once for reuse across features."""
    semantic_model = model or load_sentence_transformer(model_name)
    deterministic_index = prepare_feature_matcher(kb, terminology)
    documents = tuple(
        _semantic_document(pieces) for pieces in deterministic_index.rule_pieces
    )
    embeddings = _encode_normalized(semantic_model, documents)
    try:
        package_version = version("sentence-transformers")
    except Exception:  # pragma: no cover - only for injected test doubles
        package_version = "unknown"
    return SemanticFeatureIndex(
        model_name=model_name,
        package_version=package_version,
        model=semantic_model,
        kb=kb,
        terminology=terminology,
        deterministic_index=deterministic_index,
        canonical_features=tuple(rule["feature"] for rule in kb["feature_rules"]),
        semantic_documents=documents,
        embeddings=embeddings,
        embedding_dimension=int(embeddings.shape[1]),
    )


def _prepare_semantic_input(
    feature_name: str,
    feature_description: str | None,
    index: SemanticFeatureIndex,
) -> dict[str, Any]:
    if not isinstance(feature_name, str) or not feature_name.strip():
        raise ValueError("feature_name must be a non-empty string")
    description = (
        ""
        if feature_description is None
        or (isinstance(feature_description, float) and math.isnan(feature_description))
        else str(feature_description)
    )
    tables = index.deterministic_index.terminology_tables
    processed_name = _expand_terminology(
        feature_name,
        index.terminology,
        context_text=description,
        terminology_tables=tables,
    )
    processed_description = _expand_terminology(
        description,
        index.terminology,
        terminology_tables=tables,
    )
    unresolved = [
        *processed_name["unresolved_ambiguities"],
        *processed_description["unresolved_ambiguities"],
    ]
    ambiguity_text = ". ".join(
        f"possible meaning {candidate}"
        for item in unresolved
        for candidate in item["candidates"]
    )
    semantic_text = ". ".join(
        part
        for part in (
            processed_name["expanded_text"],
            processed_description["expanded_text"],
            ambiguity_text,
        )
        if part
    )
    return {
        "original_feature_name": feature_name,
        "original_description": description,
        "normalized_feature_name": processed_name["normalized_text"],
        "expanded_feature_name": processed_name["expanded_text"],
        "expanded_description": processed_description["expanded_text"],
        "processed_feature_text": semantic_text,
        "unresolved_ambiguous_terminology": unresolved,
    }


def _embedding_candidates(
    query_embedding: np.ndarray,
    index: SemanticFeatureIndex,
    *,
    top_n: int,
) -> list[dict[str, Any]]:
    # Both query and KB matrices are L2-normalized by model.encode, therefore
    # their dot product is cosine similarity.
    scores = np.asarray(index.embeddings @ query_embedding, dtype=float)
    order = np.argsort(-scores, kind="stable")[:top_n]
    rules = index.kb["feature_rules"]
    return [
        {
            "rank": rank,
            "canonical_feature": rules[position]["feature"],
            "feature_family": rules[position]["feature_family"],
            "cosine_similarity": round(float(scores[position]), 6),
            "semantic_document": index.semantic_documents[position],
            "expected_direction": rules[position]["expected_direction"],
            "knowledge_strength": rules[position]["knowledge_strength"],
            "representation_evidence": "not_determined",
        }
        for rank, position in enumerate(order, start=1)
    ]


def _exact_match(prepared_input: dict[str, Any], index: SemanticFeatureIndex) -> dict[str, Any] | None:
    matches = index.deterministic_index.exact_index.get(
        prepared_input["expanded_feature_name"], []
    )
    if not matches or len({match["canonical_feature"] for match in matches}) != 1:
        return None
    return matches[0]


def _assemble_result(
    prepared_input: dict[str, Any],
    embedding_candidates: list[dict[str, Any]],
    index: SemanticFeatureIndex,
    *,
    preserve_exact: bool,
) -> dict[str, Any]:
    exact = _exact_match(prepared_input, index) if preserve_exact else None
    embedding_top1 = embedding_candidates[0] if embedding_candidates else None
    embedding_top2 = embedding_candidates[1] if len(embedding_candidates) > 1 else None
    embedding_gap = (
        round(
            embedding_top1["cosine_similarity"] - embedding_top2["cosine_similarity"],
            6,
        )
        if embedding_top1 and embedding_top2
        else None
    )

    if exact:
        primary_candidates = [
            {
                "rank": 1,
                "canonical_feature": exact["canonical_feature"],
                "feature_family": exact["feature_family"],
                "cosine_similarity": None,
                "semantic_document": None,
                "expected_direction": exact["expected_direction"],
                "knowledge_strength": exact["knowledge_strength"],
                "representation_evidence": (
                    "inverse" if exact["is_inverse_representation"] else "normal"
                ),
            }
        ]
        match_status = "exact_match"
        match_method = "deterministic_exact"
    else:
        primary_candidates = embedding_candidates
        match_status = "semantic_candidate"
        match_method = "sentence_embedding_cosine"

    top1 = primary_candidates[0] if primary_candidates else None
    top2 = primary_candidates[1] if len(primary_candidates) > 1 else None
    top3 = primary_candidates[2] if len(primary_candidates) > 2 else None
    primary_gap = (
        round(top1["cosine_similarity"] - top2["cosine_similarity"], 6)
        if top1 and top2
        else None
    )
    return {
        **prepared_input,
        "match_status": match_status,
        "match_method": match_method,
        "deterministic_match": exact,
        "top_1_canonical_feature": top1["canonical_feature"] if top1 else None,
        "top_1_cosine_similarity": top1["cosine_similarity"] if top1 else None,
        "top_2_canonical_feature": top2["canonical_feature"] if top2 else None,
        "top_2_cosine_similarity": top2["cosine_similarity"] if top2 else None,
        "top_3_canonical_feature": top3["canonical_feature"] if top3 else None,
        "top_3_cosine_similarity": top3["cosine_similarity"] if top3 else None,
        "top_1_minus_top_2_similarity_gap": primary_gap,
        "top_3_candidates": primary_candidates[:3],
        "embedding_only_top_1": embedding_top1["canonical_feature"] if embedding_top1 else None,
        "embedding_only_top_1_score": (
            embedding_top1["cosine_similarity"] if embedding_top1 else None
        ),
        "embedding_only_top_2": embedding_top2["canonical_feature"] if embedding_top2 else None,
        "embedding_only_top_2_score": (
            embedding_top2["cosine_similarity"] if embedding_top2 else None
        ),
        "embedding_only_top_3": (
            embedding_candidates[2]["canonical_feature"]
            if len(embedding_candidates) > 2
            else None
        ),
        "embedding_only_top_3_score": (
            embedding_candidates[2]["cosine_similarity"]
            if len(embedding_candidates) > 2
            else None
        ),
        "embedding_only_score_gap": embedding_gap,
        "embedding_only_top_3_candidates": embedding_candidates[:3],
        "semantic_model": index.model_name,
        "embedding_dimension": index.embedding_dimension,
    }


def match_feature_semantic(
    feature_name: str,
    feature_description: str | None,
    kb: dict[str, Any],
    terminology: dict[str, Any],
    semantic_index: SemanticFeatureIndex,
    top_n: int = 3,
) -> dict[str, Any]:
    """Match one feature using exact aliases followed only by cosine similarity."""
    if semantic_index.kb is not kb or semantic_index.terminology is not terminology:
        raise ValueError("semantic_index must have been built from the supplied kb and terminology")
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 3:
        raise ValueError("top_n must be an integer of at least 3")
    prepared_input = _prepare_semantic_input(
        feature_name, feature_description, semantic_index
    )
    query_embedding = _encode_normalized(
        semantic_index.model, [prepared_input["processed_feature_text"]]
    )[0]
    candidates = _embedding_candidates(
        query_embedding, semantic_index, top_n=top_n
    )
    return _assemble_result(
        prepared_input,
        candidates,
        semantic_index,
        preserve_exact=True,
    )


def match_features_semantic(
    feature_metadata: pd.DataFrame,
    kb: dict[str, Any],
    terminology: dict[str, Any],
    semantic_index: SemanticFeatureIndex,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """Batch semantic matching with one model encode call for all inputs."""
    if semantic_index.kb is not kb or semantic_index.terminology is not terminology:
        raise ValueError("semantic_index must have been built from the supplied kb and terminology")
    if not isinstance(feature_metadata, pd.DataFrame):
        raise TypeError("feature_metadata must be a pandas DataFrame")
    missing = [column for column in ("feature_name", "description") if column not in feature_metadata]
    if missing:
        raise ValueError(f"feature_metadata is missing column(s): {', '.join(missing)}")
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 3:
        raise ValueError("top_n must be an integer of at least 3")

    prepared_inputs = [
        _prepare_semantic_input(row.feature_name, row.description, semantic_index)
        for row in feature_metadata.loc[:, ["feature_name", "description"]].itertuples(
            index=False
        )
    ]
    query_embeddings = _encode_normalized(
        semantic_index.model,
        [item["processed_feature_text"] for item in prepared_inputs],
    )
    return [
        _assemble_result(
            prepared_input,
            _embedding_candidates(query_embedding, semantic_index, top_n=top_n),
            semantic_index,
            preserve_exact=True,
        )
        for prepared_input, query_embedding in zip(
            prepared_inputs, query_embeddings, strict=True
        )
    ]


__all__ = [
    "DEFAULT_SEMANTIC_MODEL",
    "SemanticFeatureIndex",
    "build_semantic_index",
    "load_sentence_transformer",
    "match_feature_semantic",
    "match_features_semantic",
]
