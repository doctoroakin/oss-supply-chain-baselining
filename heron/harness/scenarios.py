"""Scenario loader and validator for the evaluation harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCENARIOS_DIR = PROJECT_ROOT / "harness" / "scenarios"


@dataclass
class PositiveScenario:
    id: str
    repo: str
    target_commit_sha: str
    threat_model: str
    pending: bool = False
    notes: str = ""


@dataclass
class NegativeScenario:
    id: str
    repo: str
    contributor_id: str
    appropriate_for_threat_models: list[str] = field(default_factory=list)
    pending: bool = False
    display_name: str = ""
    notes: str = ""


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "scenarios" not in data:
        raise ValueError(f"Scenario file malformed (missing 'scenarios' key): {path}")
    return data


def load_positives(path: Path | None = None) -> list[PositiveScenario]:
    path = path or SCENARIOS_DIR / "positives.yaml"
    data = _load_yaml(path)
    scenarios = []
    for i, entry in enumerate(data["scenarios"]):
        required = ("id", "repo", "target_commit_sha", "threat_model")
        missing = [k for k in required if k not in entry]
        if missing:
            raise ValueError(
                f"Positive scenario #{i} in {path} missing required fields: {missing}"
            )
        scenarios.append(PositiveScenario(
            id=entry["id"],
            repo=entry["repo"],
            target_commit_sha=entry["target_commit_sha"],
            threat_model=entry["threat_model"],
            pending=entry.get("pending", False),
            notes=entry.get("notes", ""),
        ))
    return scenarios


def load_negatives(path: Path | None = None) -> list[NegativeScenario]:
    path = path or SCENARIOS_DIR / "negatives.yaml"
    data = _load_yaml(path)
    scenarios = []
    for i, entry in enumerate(data["scenarios"]):
        required = ("id", "repo", "contributor_id")
        missing = [k for k in required if k not in entry]
        if missing:
            raise ValueError(
                f"Negative scenario #{i} in {path} missing required fields: {missing}"
            )
        scenarios.append(NegativeScenario(
            id=entry["id"],
            repo=entry["repo"],
            contributor_id=entry["contributor_id"],
            appropriate_for_threat_models=entry.get("appropriate_for_threat_models", []),
            pending=entry.get("pending", False),
            display_name=entry.get("display_name", ""),
            notes=entry.get("notes", ""),
        ))
    return scenarios


def validate_scenarios(
    positives: list[PositiveScenario],
    negatives: list[NegativeScenario],
) -> None:
    """Cross-validate positive and negative scenarios. Raises on conflict."""
    # Check for duplicate scenario IDs
    all_ids = [s.id for s in positives] + [s.id for s in negatives]
    seen_ids: set[str] = set()
    for sid in all_ids:
        if sid in seen_ids:
            raise ValueError(f"Duplicate scenario id: {sid}")
        seen_ids.add(sid)

    # Check for duplicate SHAs across positives
    pos_shas: set[str] = set()
    for s in positives:
        if s.target_commit_sha in pos_shas:
            raise ValueError(f"Duplicate target_commit_sha: {s.target_commit_sha}")
        pos_shas.add(s.target_commit_sha)

    # Build set of positive-commit author developer_ids (resolved at run time)
    # This validation is done in runner.py after loading data, not here.
