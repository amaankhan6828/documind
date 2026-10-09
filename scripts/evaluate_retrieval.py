"""Evaluate dense, lexical, and hybrid retrieval against manually judged labels."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from backend.app.retrieval.evaluation import RetrievalBenchmark, evaluate_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--candidate-limit", type=int, default=20)
    parser.add_argument("--rrf-k", type=int, default=60)
    args = parser.parse_args()

    benchmark = RetrievalBenchmark.model_validate_json(
        args.benchmark.read_text(encoding="utf-8")
    )
    report = evaluate_benchmark(
        args.tenant_id,
        benchmark,
        k=args.k,
        candidate_limit=args.candidate_limit,
        rrf_k=args.rrf_k,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
