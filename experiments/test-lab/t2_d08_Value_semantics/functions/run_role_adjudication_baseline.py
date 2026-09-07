"""Run the versioned 75-case value-semantics role-adjudication baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .kb_loader import load_terminology, load_value_semantics_kb
from .llm_role_adjudication import (
    OpenAIRoleAdjudicator, configured_model, create_openai_client, load_adjudication_prompt,
)
from .role_adjudication_validation import (
    adjudication_metrics, attach_ground_truth, benchmark_role_coverage, retrieval_metrics,
    run_benchmark_predictions,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = EXPERIMENT_ROOT.parent / "t2_d11_dir_consistency" / ".env"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE,
                        help="Environment file; defaults to the existing t2_d11 .env.")
    parser.add_argument("--output-dir", type=Path, default=EXPERIMENT_ROOT / "output")
    parser.add_argument("--checkpoint", type=Path,
                        default=EXPERIMENT_ROOT / ".runtime" / "role_adjudication_checkpoint_v0_2.jsonl")
    args = parser.parse_args()

    kb_path = EXPERIMENT_ROOT / "kb" / "value_semantics_kb_v0_2.yaml"
    terminology_path = EXPERIMENT_ROOT / "kb" / "credit_risk_abbreviations_v0_3.yaml"
    benchmark_path = EXPERIMENT_ROOT / "inputs" / "test_fixtures" / "role_matching_benchmark_v0_2.csv"
    prompt_path = EXPERIMENT_ROOT / "prompts" / "value_semantics_role_adjudication_v0_2.txt"
    kb = load_value_semantics_kb(kb_path)
    terminology = load_terminology(terminology_path)
    benchmark = pd.read_csv(
        benchmark_path,
        keep_default_na=False,
    )
    prompt = load_adjudication_prompt(
        prompt_path
    )
    model = configured_model(args.env_file)
    adjudicator = OpenAIRoleAdjudicator(
        client=create_openai_client(args.env_file), prompt=prompt, model=model,
    )
    def progress(index: int, total: int, name: str, used_llm: bool) -> None:
        route = "LLM" if used_llm else "exact bypass"
        print(f"[{index:02d}/{total:02d}] {name}: {route}", flush=True)

    predictions = run_benchmark_predictions(
        benchmark, kb, terminology, adjudicator, checkpoint_path=args.checkpoint,
        progress=progress,
    )
    evaluation = attach_ground_truth(predictions, benchmark)
    metrics = adjudication_metrics(evaluation)
    coverage = benchmark_role_coverage(benchmark, kb)
    retrieval = retrieval_metrics(benchmark, kb, terminology)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "results": args.output_dir / "role_adjudication_results_v0_2.csv",
        "metrics": args.output_dir / "role_adjudication_metrics_v0_2.csv",
        "coverage": args.output_dir / "role_benchmark_coverage_v0_2.csv",
        "retrieval": args.output_dir / "role_retrieval_metrics_v0_2.csv",
        "mismatches": args.output_dir / "role_adjudication_mismatches_v0_2.csv",
    }
    evaluation.to_csv(paths["results"], index=False)
    metrics.to_csv(paths["metrics"], index=False)
    coverage.to_csv(paths["coverage"], index=False)
    retrieval.to_csv(paths["retrieval"], index=False)
    evaluation.loc[~evaluation["case_correct"]].to_csv(paths["mismatches"], index=False)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
        "model_requested": model, "kb_version": kb["metadata"]["version"],
        "artifacts": {path.name: digest(path) for path in (kb_path, terminology_path, benchmark_path, prompt_path)},
        "case_count": len(benchmark), "llm_case_count": int(predictions["adjudication_used"].sum()),
        "checkpoint_hit_count": int(predictions["checkpoint_hits"].sum()),
    }
    manifest_path = args.output_dir / "role_adjudication_run_manifest_v0_2.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    paths["manifest"] = manifest_path
    print(metrics.to_string(index=False), flush=True)
    for label, path in paths.items():
        print(f"Wrote {label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
