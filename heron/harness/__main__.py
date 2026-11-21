"""CLI entry point for the evaluation harness.

Usage:
    python -m heron.harness run --detector mock --threshold 0.5
    python -m heron.harness run --detector mock --threshold-sweep 0.0:1.0:0.1
    python -m heron.harness run --detector mock --scenarios positives
    python -m heron.harness run --detector mock --threat-model impersonation
"""

from __future__ import annotations

import argparse
import sys

from heron.harness.runner import run_harness


DETECTORS = {
    "mock": None,  # lazy-loaded below
}


def _get_detector(name: str):
    if name == "mock":
        from heron.harness.mock_detector import mock_detector
        return mock_detector
    raise ValueError(f"Unknown detector: {name}. Available: {list(DETECTORS.keys())}")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m heron.harness",
        description="Evaluation harness for behavioral baseline detectors",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run the harness")
    run_parser.add_argument("--detector", required=True, help="Detector name (e.g. 'mock')")
    run_parser.add_argument("--threshold", type=float, default=None, help="Confidence threshold for firing")
    run_parser.add_argument("--threshold-sweep", type=str, default=None, help="Sweep spec: start:stop:step (e.g. 0.0:1.0:0.1)")
    run_parser.add_argument("--scenarios", type=str, default=None, choices=["positives", "negatives"], help="Run only positive or negative scenarios")
    run_parser.add_argument("--threat-model", type=str, default=None, help="Filter positives and negatives to a specific threat model")

    args = parser.parse_args()

    if args.command != "run":
        parser.print_help()
        return 1

    detector_fn = _get_detector(args.detector)

    return run_harness(
        detector_fn=detector_fn,
        detector_name=args.detector,
        threshold=args.threshold,
        sweep_spec=args.threshold_sweep,
        scenario_filter=args.scenarios,
        threat_model_filter=args.threat_model,
    )


if __name__ == "__main__":
    sys.exit(main())
