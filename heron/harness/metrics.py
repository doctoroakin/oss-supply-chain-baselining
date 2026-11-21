"""Metric computation for the evaluation harness."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScenarioResult:
    scenario_id: str
    scenario_type: str  # "positive" or "negative"
    repo: str
    threat_model: str  # empty for negatives
    target_sha: str
    confidence: float
    fired: bool
    status: str  # "caught", "missed", "true_negative", "false_positive", "error", "pending"
    evidence: list[dict] = field(default_factory=list)
    detector_version: str = ""
    wall_clock_ms: float = 0.0
    error_message: str = ""


def classify_result(
    result: ScenarioResult,
    threshold: float,
) -> ScenarioResult:
    """Set fired and status fields based on threshold."""
    result.fired = result.confidence >= threshold
    if result.scenario_type == "positive":
        result.status = "caught" if result.fired else "missed"
    else:
        result.status = "false_positive" if result.fired else "true_negative"
    return result


def compute_summary(
    results: list[ScenarioResult],
    threshold: float,
    pending_count: int,
    detector_name: str,
    detector_version: str,
) -> dict:
    """Compute the summary.json structure from classified results."""
    positives = [r for r in results if r.scenario_type == "positive"]
    negatives = [r for r in results if r.scenario_type == "negative"]

    caught = [r for r in positives if r.status == "caught"]
    missed = [r for r in positives if r.status == "missed"]
    fp = [r for r in negatives if r.status == "false_positive"]
    tn = [r for r in negatives if r.status == "true_negative"]
    errors = [r for r in results if r.status == "error"]

    n_pos = len(positives)
    n_neg = len(negatives)

    recall = len(caught) / n_pos if n_pos > 0 else 0.0
    fpr = len(fp) / n_neg if n_neg > 0 else 0.0

    # Per-threat-model breakdown
    threat_models: dict[str, dict] = {}
    pos_by_tm: dict[str, list[ScenarioResult]] = {}
    for r in positives:
        pos_by_tm.setdefault(r.threat_model, []).append(r)

    for tm, tm_results in pos_by_tm.items():
        tm_caught = sum(1 for r in tm_results if r.status == "caught")
        tm_total = len(tm_results)
        threat_models[tm] = {
            "recall": tm_caught / tm_total if tm_total > 0 else 0.0,
            "fpr": fpr,  # FPR is global (negatives aren't threat-model-specific)
            "n_pos": tm_total,
            "n_neg": n_neg,
        }

    # Check for pending threat models (from pending scenarios, handled by caller)
    # The caller passes pending_count; pending threat models are added in runner.py

    return {
        "detector": detector_name,
        "detector_version": detector_version,
        "threshold": threshold,
        "scenarios_run": len(results),
        "scenarios_pending": pending_count,
        "scenarios_errored": len(errors),
        "positives": {
            "total": n_pos,
            "caught": len(caught),
            "missed": len(missed),
            "recall": round(recall, 4),
        },
        "negatives": {
            "total": n_neg,
            "false_positives": len(fp),
            "true_negatives": len(tn),
            "false_positive_rate": round(fpr, 4),
        },
        "per_threat_model": threat_models,
    }


def threshold_sweep(
    results: list[ScenarioResult],
    start: float,
    stop: float,
    step: float,
    pending_count: int,
    detector_name: str,
    detector_version: str,
) -> list[dict]:
    """Re-bucket existing results at multiple thresholds without re-running the detector."""
    curve = []
    threshold = start
    while threshold <= stop + 1e-9:
        t = round(threshold, 6)
        # Re-classify at this threshold
        for r in results:
            classify_result(r, t)
        summary = compute_summary(results, t, pending_count, detector_name, detector_version)

        positives = summary["positives"]
        negatives = summary["negatives"]
        precision = (
            positives["caught"] / (positives["caught"] + negatives["false_positives"])
            if (positives["caught"] + negatives["false_positives"]) > 0
            else 0.0
        )

        curve.append({
            "threshold": t,
            "precision": round(precision, 4),
            "recall": positives["recall"],
            "fpr": negatives["false_positive_rate"],
            "caught": positives["caught"],
            "false_positives": negatives["false_positives"],
        })
        threshold += step

    return curve
