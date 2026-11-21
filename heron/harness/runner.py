"""Orchestration loop for the evaluation harness."""

from __future__ import annotations

import logging
import time
from typing import Callable

from heron.data_loader import load_repo, list_available_repos
from heron.harness.metrics import ScenarioResult, classify_result, compute_summary, threshold_sweep
from heron.harness.scenarios import (
    PositiveScenario,
    NegativeScenario,
    load_positives,
    load_negatives,
    validate_scenarios,
)
from heron.harness.reports import (
    generate_run_id,
    write_summary,
    write_per_scenario,
    write_confidence_log,
    write_pr_curve,
    print_console_summary,
    OUTPUT_DIR,
)

logger = logging.getLogger(__name__)

DetectorFn = Callable[[dict, dict], dict]


def _build_enriched_commit(commit_row: dict) -> dict:
    """Build an enriched_commit dict from a raw commit row.

    For Spec 002 this is just commit_info + empty enrichment block.
    Real enrichment ships in Spec 004.
    """
    return {
        "commit_info": commit_row,
        "enrichment": {},
    }


def _build_context(repo_data, contributor_dev_id: str, before_timestamp) -> dict:
    """Build context dict with contributor's prior commit history."""
    dev_edges = repo_data.developer_edges
    commits = repo_data.commits

    author_edges = dev_edges[
        (dev_edges["developer_id"] == contributor_dev_id)
        & (dev_edges["role"] == "author")
    ]
    author_commit_ids = set(author_edges["commit_id"])
    author_commits = commits[commits["commit_id"].isin(author_commit_ids)]

    if before_timestamp is not None:
        prior = author_commits[author_commits["authored_at_utc"] < before_timestamp]
    else:
        prior = author_commits

    return {
        "prior_commits": prior.to_dict("records"),
        "contributor_id": contributor_dev_id,
        "prior_commit_count": len(prior),
    }


def _validate_no_overlap(positives, negatives, repo_cache):
    """Validate that no negative contributor authored a positive commit."""
    # Build set of (repo, developer_id) for positive commit authors
    pos_authors: set[tuple[str, str]] = set()
    for s in positives:
        if s.pending:
            continue
        if s.repo not in repo_cache:
            continue
        repo_data = repo_cache[s.repo]
        commit_row = repo_data.commits[
            repo_data.commits["commit_id"] == s.target_commit_sha
        ]
        if len(commit_row) > 0:
            dev_id = commit_row.iloc[0]["author_developer_id"]
            pos_authors.add((s.repo, dev_id))

    for s in negatives:
        if s.pending:
            continue
        if (s.repo, s.contributor_id) in pos_authors:
            raise ValueError(
                f"Negative scenario '{s.id}' contributor {s.contributor_id} "
                f"authored a positive scenario commit in {s.repo}. "
                f"Remove from negatives or positives."
            )


def run_harness(
    detector_fn: DetectorFn,
    detector_name: str,
    threshold: float | None = None,
    sweep_spec: str | None = None,
    scenario_filter: str | None = None,
    threat_model_filter: str | None = None,
) -> int:
    """Run the evaluation harness. Returns exit code (0 = success)."""
    # Load scenarios
    positives = load_positives()
    negatives = load_negatives()
    validate_scenarios(positives, negatives)

    # Apply filters
    if scenario_filter == "positives":
        negatives = []
    elif scenario_filter == "negatives":
        positives = []

    if threat_model_filter:
        positives = [s for s in positives if s.threat_model == threat_model_filter]
        negatives = [
            s for s in negatives
            if not s.appropriate_for_threat_models
            or threat_model_filter in s.appropriate_for_threat_models
        ]

    # Separate pending
    active_positives = [s for s in positives if not s.pending]
    pending_positives = [s for s in positives if s.pending]
    active_negatives = [s for s in negatives if not s.pending]
    pending_negatives = [s for s in negatives if s.pending]
    all_pending = pending_positives + pending_negatives

    # Log pending
    for s in all_pending:
        logger.info("Skipping pending scenario: %s", s.id)

    # Load repo data (only repos we need)
    available = set(list_available_repos())
    needed_repos: set[str] = set()
    for s in active_positives:
        needed_repos.add(s.repo)
    for s in active_negatives:
        needed_repos.add(s.repo)

    repo_cache = {}
    for repo_slug in needed_repos:
        if repo_slug not in available:
            logger.warning("Repo '%s' not available locally, skipping its scenarios", repo_slug)
            continue
        repo_cache[repo_slug] = load_repo(repo_slug)

    # Validate no overlap between positives and negatives
    _validate_no_overlap(positives, negatives, repo_cache)

    # Run detector on each active scenario
    results: list[ScenarioResult] = []

    # Positive scenarios
    for s in active_positives:
        if s.repo not in repo_cache:
            results.append(ScenarioResult(
                scenario_id=s.id, scenario_type="positive", repo=s.repo,
                threat_model=s.threat_model, target_sha=s.target_commit_sha,
                confidence=0.0, fired=False, status="error",
                error_message=f"Repo '{s.repo}' not available locally",
            ))
            continue

        repo_data = repo_cache[s.repo]
        commit_rows = repo_data.commits[
            repo_data.commits["commit_id"] == s.target_commit_sha
        ]
        if len(commit_rows) == 0:
            results.append(ScenarioResult(
                scenario_id=s.id, scenario_type="positive", repo=s.repo,
                threat_model=s.threat_model, target_sha=s.target_commit_sha,
                confidence=0.0, fired=False, status="error",
                error_message=f"Commit {s.target_commit_sha} not found in {s.repo}",
            ))
            continue

        commit_row = commit_rows.iloc[0].to_dict()
        author_dev_id = commit_row["author_developer_id"]
        context = _build_context(repo_data, author_dev_id, commit_row.get("authored_at_utc"))
        enriched = _build_enriched_commit(commit_row)

        try:
            t0 = time.monotonic()
            det_result = detector_fn(enriched, context)
            elapsed_ms = (time.monotonic() - t0) * 1000
        except Exception as exc:
            results.append(ScenarioResult(
                scenario_id=s.id, scenario_type="positive", repo=s.repo,
                threat_model=s.threat_model, target_sha=s.target_commit_sha,
                confidence=0.0, fired=False, status="error",
                error_message=str(exc),
            ))
            continue

        results.append(ScenarioResult(
            scenario_id=s.id, scenario_type="positive", repo=s.repo,
            threat_model=s.threat_model, target_sha=s.target_commit_sha,
            confidence=det_result["confidence"],
            fired=False, status="",
            evidence=det_result.get("evidence", []),
            detector_version=det_result.get("detector_version", ""),
            wall_clock_ms=elapsed_ms,
        ))

    # Negative scenarios
    for s in active_negatives:
        if s.repo not in repo_cache:
            results.append(ScenarioResult(
                scenario_id=s.id, scenario_type="negative", repo=s.repo,
                threat_model="", target_sha="",
                confidence=0.0, fired=False, status="error",
                error_message=f"Repo '{s.repo}' not available locally",
            ))
            continue

        repo_data = repo_cache[s.repo]

        # For negatives, evaluate the contributor's latest commit
        dev_edges = repo_data.developer_edges
        author_edges = dev_edges[
            (dev_edges["developer_id"] == s.contributor_id)
            & (dev_edges["role"] == "author")
        ]
        author_commit_ids = set(author_edges["commit_id"])
        author_commits = repo_data.commits[
            repo_data.commits["commit_id"].isin(author_commit_ids)
        ].sort_values("authored_at_utc")

        if len(author_commits) == 0:
            results.append(ScenarioResult(
                scenario_id=s.id, scenario_type="negative", repo=s.repo,
                threat_model="", target_sha="",
                confidence=0.0, fired=False, status="error",
                error_message=f"No commits found for contributor {s.contributor_id} in {s.repo}",
            ))
            continue

        # Use the latest commit as the target
        latest_row = author_commits.iloc[-1].to_dict()
        target_sha = latest_row["commit_id"]
        context = _build_context(repo_data, s.contributor_id, latest_row.get("authored_at_utc"))
        enriched = _build_enriched_commit(latest_row)

        try:
            t0 = time.monotonic()
            det_result = detector_fn(enriched, context)
            elapsed_ms = (time.monotonic() - t0) * 1000
        except Exception as exc:
            results.append(ScenarioResult(
                scenario_id=s.id, scenario_type="negative", repo=s.repo,
                threat_model="", target_sha=target_sha,
                confidence=0.0, fired=False, status="error",
                error_message=str(exc),
            ))
            continue

        results.append(ScenarioResult(
            scenario_id=s.id, scenario_type="negative", repo=s.repo,
            threat_model="", target_sha=target_sha,
            confidence=det_result["confidence"],
            fired=False, status="",
            evidence=det_result.get("evidence", []),
            detector_version=det_result.get("detector_version", ""),
            wall_clock_ms=elapsed_ms,
        ))

    # Determine detector version from results
    det_versions = {r.detector_version for r in results if r.detector_version}
    det_version = det_versions.pop() if len(det_versions) == 1 else ",".join(sorted(det_versions)) or "unknown"

    # Compute pending threat model info
    pending_tm_info = {}
    for s in pending_positives:
        pending_tm_info.setdefault(s.threat_model, {"status": "pending", "n_pos": 0})
        pending_tm_info[s.threat_model]["n_pos"] += 1

    run_id = generate_run_id()
    run_dir = OUTPUT_DIR / run_id

    pr_curve = None

    if sweep_spec:
        # Parse sweep spec: start:stop:step
        parts = sweep_spec.split(":")
        start, stop, step = float(parts[0]), float(parts[1]), float(parts[2])

        # Classify at default threshold first (midpoint of sweep)
        default_t = threshold if threshold is not None else (start + stop) / 2
        for r in results:
            classify_result(r, default_t)

        pr_curve = threshold_sweep(results, start, stop, step, len(all_pending), detector_name, det_version)

        # Final classification at the default threshold for reports
        for r in results:
            classify_result(r, default_t)

        summary = compute_summary(results, default_t, len(all_pending), detector_name, det_version)
        summary["per_threat_model"].update(pending_tm_info)

        write_summary(run_id, summary)
        write_per_scenario(run_id, results)
        write_confidence_log(run_id, results)
        write_pr_curve(run_id, pr_curve)
        print_console_summary(summary, results, all_pending, run_dir, pr_curve)
    else:
        t = threshold if threshold is not None else 0.5
        for r in results:
            classify_result(r, t)

        summary = compute_summary(results, t, len(all_pending), detector_name, det_version)
        summary["per_threat_model"].update(pending_tm_info)

        write_summary(run_id, summary)
        write_per_scenario(run_id, results)
        write_confidence_log(run_id, results)
        print_console_summary(summary, results, all_pending, run_dir)

    return 0
