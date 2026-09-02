"""Run and export the untuned GPT-5.4-mini 75-case adjudication baseline."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .feature_matching import load_kb, load_terminology
from .feature_matching_validation import load_validation_dataset
from .llm_semantic_adjudication import (
    DEFAULT_MODEL,
    CONTRACT_VERSION_V0_1,
    CONTRACT_VERSION_V0_2,
    OpenAISemanticAdjudicator,
    PROMPT_VERSION_V0_1,
    PROMPT_VERSION_V0_2,
    configured_model,
    create_openai_client,
    load_adjudication_prompt,
)
from .llm_semantic_adjudication_validation import (
    adjudication_error_tables,
    adjudication_metrics,
    attach_adjudication_ground_truth,
    combined_error_export,
    compare_adjudication_versions,
    run_adjudication_predictions,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
VERSION_CONFIG = {
    "v0_1": (PROMPT_VERSION_V0_1, CONTRACT_VERSION_V0_1),
    "v0_2": (PROMPT_VERSION_V0_2, CONTRACT_VERSION_V0_2),
}
KB_VERSION_CONFIG = {
    "v0_2": "pd_directionality_kb_v0_2.yaml",
    "v0_3": "pd_directionality_kb_v0_3.yaml",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment-version",
        choices=tuple(VERSION_CONFIG),
        default="v0_2",
        help="Select the fixed prompt and audit contract version.",
    )
    parser.add_argument(
        "--kb-version",
        choices=tuple(KB_VERSION_CONFIG),
        default="v0_3",
        help="Select the Knowledge Base version independently of the prompt version.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=4,
        help="Maximum number of simultaneous semantic adjudication requests.",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="Override the persistent JSONL checkpoint path.",
    )
    args = parser.parse_args()

    prompt_version, contract_version = VERSION_CONFIG[args.experiment_version]
    output_version = f"{args.experiment_version}_kb_{args.kb_version}"
    output_path = args.output or (
        EXPERIMENT_ROOT
        / "output"
        / f"llm_semantic_adjudication_results_{output_version}.csv"
    )

    env_path = EXPERIMENT_ROOT / ".env"
    model = configured_model(env_path)
    if model != DEFAULT_MODEL:
        raise RuntimeError(
            f"This fixed baseline requires {DEFAULT_MODEL!r}; configured deployment is {model!r}"
        )
    prompt = load_adjudication_prompt(
        EXPERIMENT_ROOT / "prompts" / f"semantic_feature_adjudication_{args.experiment_version}.txt"
    )
    client = create_openai_client(env_path)
    adjudicator = OpenAISemanticAdjudicator(
        client=client,
        prompt=prompt,
        model=model,
        prompt_version=prompt_version,
        contract_version=contract_version,
    )
    kb = load_kb(EXPERIMENT_ROOT / "kb" / KB_VERSION_CONFIG[args.kb_version])
    terminology = load_terminology(
        EXPERIMENT_ROOT / "kb" / "credit_risk_abbreviations_v0_2.yaml"
    )
    validation = load_validation_dataset(
        EXPERIMENT_ROOT / "inputs" / "feature_matching_validation_v0_1.csv"
    )

    def progress(index: int, total: int, name: str, used_llm: bool) -> None:
        route = "LLM" if used_llm else "exact bypass"
        print(f"[{index:02d}/{total:02d}] {name}: {route}", flush=True)

    predictions = run_adjudication_predictions(
        validation,
        kb,
        terminology,
        adjudicator,
        progress=progress,
        max_concurrency=args.max_concurrency,
        checkpoint_path=(
            args.checkpoint
            or EXPERIMENT_ROOT
            / ".runtime"
            / f"adjudication_checkpoint_{output_version}.jsonl"
        ),
    )
    evaluation = attach_adjudication_ground_truth(predictions, validation)
    metrics = adjudication_metrics(evaluation)
    errors = combined_error_export(adjudication_error_tables(evaluation))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    evaluation.to_csv(output_path, index=False)
    metrics_path = output_path.with_name(
        f"llm_semantic_adjudication_metrics_{output_version}.csv"
    )
    errors_path = output_path.with_name(
        f"llm_semantic_adjudication_errors_{output_version}.csv"
    )
    metrics.to_csv(metrics_path, index=False)
    errors.to_csv(errors_path, index=False)

    if args.experiment_version == "v0_2" and args.kb_version == "v0_2":
        baseline_path = output_path.with_name("llm_semantic_adjudication_results_v0_1.csv")
        if baseline_path.is_file():
            baseline = pd.read_csv(baseline_path, keep_default_na=False)
            comparison, decision_changes = compare_adjudication_versions(
                baseline, evaluation
            )
            comparison_path = output_path.with_name(
                "llm_semantic_adjudication_comparison_v0_1_v0_2.csv"
            )
            changes_path = output_path.with_name(
                "llm_semantic_adjudication_decision_changes_v0_1_v0_2.csv"
            )
            comparison.to_csv(comparison_path, index=False)
            decision_changes.to_csv(changes_path, index=False)
            print(f"Wrote comparison: {comparison_path}", flush=True)
            print(f"Wrote decision changes: {changes_path}", flush=True)
    print(metrics.to_string(index=False), flush=True)
    print(f"Wrote results: {output_path}", flush=True)
    print(f"Wrote metrics: {metrics_path}", flush=True)
    print(f"Wrote errors: {errors_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
