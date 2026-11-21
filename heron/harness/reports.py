"""Report writers for the evaluation harness."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from heron.harness.metrics import ScenarioResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "harness" / "runs"


def _run_dir(run_id: str) -> Path:
    d = OUTPUT_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def write_summary(run_id: str, summary: dict) -> Path:
    summary["run_id"] = run_id
    path = _run_dir(run_id) / "summary.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return path


def write_per_scenario(run_id: str, results: list[ScenarioResult]) -> Path:
    path = _run_dir(run_id) / "per_scenario.csv"
    fieldnames = [
        "scenario_id", "scenario_type", "repo", "threat_model",
        "target_sha", "confidence", "fired", "status",
        "detector_version", "wall_clock_ms", "error_message",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "scenario_id": r.scenario_id,
                "scenario_type": r.scenario_type,
                "repo": r.repo,
                "threat_model": r.threat_model,
                "target_sha": r.target_sha,
                "confidence": r.confidence,
                "fired": r.fired,
                "status": r.status,
                "detector_version": r.detector_version,
                "wall_clock_ms": round(r.wall_clock_ms, 2),
                "error_message": r.error_message,
            })
    return path


def write_confidence_log(run_id: str, results: list[ScenarioResult]) -> Path:
    path = _run_dir(run_id) / "confidence_log.csv"
    fieldnames = [
        "scenario_id", "scenario_type", "confidence", "fired", "status",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "scenario_id": r.scenario_id,
                "scenario_type": r.scenario_type,
                "confidence": r.confidence,
                "fired": r.fired,
                "status": r.status,
            })
    return path


def write_pr_curve(run_id: str, curve: list[dict]) -> Path:
    path = _run_dir(run_id) / "pr_curve.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(curve, f, indent=2)
    return path


def print_console_summary(
    summary: dict,
    results: list[ScenarioResult],
    pending_scenarios: list,
    run_dir: Path,
    pr_curve: list[dict] | None = None,
) -> None:
    """Print human-readable summary to stdout."""
    pos = summary["positives"]
    neg = summary["negatives"]

    print()
    print("=" * 60)
    print(f"  HARNESS RUN: {summary.get('run_id', 'N/A')}")
    print(f"  Detector: {summary['detector']} v{summary['detector_version']}")
    print(f"  Threshold: {summary['threshold']}")
    print("=" * 60)

    print(f"\n  Scenarios run: {summary['scenarios_run']}")
    print(f"  Scenarios pending: {summary['scenarios_pending']}")
    if summary.get("scenarios_errored", 0) > 0:
        print(f"  Scenarios errored: {summary['scenarios_errored']}")

    print(f"\n  RECALL:  {pos['recall']:.1%}  ({pos['caught']}/{pos['total']} positives caught)")
    print(f"  FPR:     {neg['false_positive_rate']:.1%}  ({neg['false_positives']}/{neg['total']} negatives fired)")

    # Per-threat-model
    print("\n  Per threat model:")
    for tm, tm_data in summary.get("per_threat_model", {}).items():
        if tm_data.get("status") == "pending":
            print(f"    {tm:20s}  PENDING ({tm_data['n_pos']} scenario(s))")
        else:
            print(f"    {tm:20s}  recall={tm_data['recall']:.1%}  n_pos={tm_data['n_pos']}")

    # Caught / missed positives
    caught = [r for r in results if r.status == "caught"]
    missed = [r for r in results if r.status == "missed"]
    if caught:
        print("\n  Caught positives:")
        for r in caught:
            print(f"    + {r.scenario_id} (conf={r.confidence:.4f})")
    if missed:
        print("\n  Missed positives:")
        for r in missed:
            print(f"    - {r.scenario_id} (conf={r.confidence:.4f})")

    # Top 3 false positives
    fps = sorted(
        [r for r in results if r.status == "false_positive"],
        key=lambda r: r.confidence,
        reverse=True,
    )
    if fps:
        print("\n  Top false positives (for review):")
        for r in fps[:3]:
            print(f"    ! {r.scenario_id} (conf={r.confidence:.4f})")

    # Pending
    if pending_scenarios:
        print("\n  Pending scenarios (skipped):")
        for s in pending_scenarios:
            print(f"    ~ {s.id}")

    # Errors
    errors = [r for r in results if r.status == "error"]
    if errors:
        print("\n  Errored scenarios:")
        for r in errors:
            print(f"    X {r.scenario_id}: {r.error_message}")

    # Threshold sweep
    if pr_curve:
        print("\n  Threshold sweep (precision / recall / FPR):")
        print(f"    {'Thresh':>7s}  {'Prec':>6s}  {'Recall':>6s}  {'FPR':>6s}  {'Caught':>6s}  {'FP':>4s}")
        print(f"    {'-'*45}")
        for point in pr_curve:
            print(f"    {point['threshold']:7.2f}  {point['precision']:6.2%}  {point['recall']:6.2%}  {point['fpr']:6.2%}  {point['caught']:>6d}  {point['false_positives']:>4d}")

    print(f"\n  Output: {run_dir}")
    print("=" * 60)
    print()
