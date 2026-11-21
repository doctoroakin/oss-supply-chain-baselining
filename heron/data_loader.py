"""Data loader for PyDriller repo shards.

Canonical entry point for all repo data access in the project.
See specs/spec-001-data-loader.md for full specification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from heron.exceptions import SchemaMismatchError, ShardNotFoundError

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHARDS_DIR = PROJECT_ROOT / "data" / "shards"

# Chunk threshold: if a CSV exceeds this size in bytes, read in chunks.
_LARGE_FILE_THRESHOLD = 500 * 1024 * 1024  # 500 MB

# Expected tables and their schemas. This is the canonical definition.
EXPECTED_TABLES: dict[str, list[str]] = {
    "repos": [
        "schema_version", "repo_id", "repo_index", "mirror_path", "cutoff_utc",
        "target_commit_count", "extracted_commit_count", "missing_commit_count",
        "included", "exclusion_reason",
    ],
    "developers": [
        "schema_version", "developer_id", "normalized_email", "normalized_name",
        "normalized_login", "display_name", "display_email",
    ],
    "commits": [
        "schema_version", "repo_id", "commit_id", "author_developer_id",
        "committer_developer_id", "author_name", "author_email", "committer_name",
        "committer_email", "authored_at_utc", "authored_tz_offset",
        "committed_at_utc", "committed_tz_offset", "message", "parent_count",
        "modified_file_count",
    ],
    "files": [
        "schema_version", "file_id", "repo_id", "normalized_path",
    ],
    "commit_parents": [
        "schema_version", "repo_id", "commit_id", "parent_commit_id", "parent_index",
    ],
    "developer_commit_edges": [
        "schema_version", "repo_id", "commit_id", "developer_id", "role",
    ],
    "commit_file_edges": [
        "schema_version", "repo_id", "commit_id", "file_id", "change_type",
        "old_path", "new_path", "normalized_path", "added_lines", "deleted_lines",
    ],
}

# Columns that should be parsed as UTC datetimes.
_TIMESTAMP_COLUMNS = {"authored_at_utc", "committed_at_utc", "cutoff_utc"}


@dataclass
class ValidationReport:
    repo_slug: str
    tables_present: list[str] = field(default_factory=list)
    tables_missing: list[str] = field(default_factory=list)
    schema_mismatches: dict[str, list[str]] = field(default_factory=dict)
    row_counts: dict[str, int] = field(default_factory=dict)
    timestamp_nat_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    is_valid: bool = False


@dataclass
class RepoDataset:
    repo_meta: pd.DataFrame
    developers: pd.DataFrame
    commits: pd.DataFrame
    files: pd.DataFrame
    commit_parents: pd.DataFrame
    developer_edges: pd.DataFrame
    file_edges: pd.DataFrame


def _shard_dir(repo_slug: str) -> Path:
    return SHARDS_DIR / repo_slug


def _read_csv(path: Path) -> pd.DataFrame:
    """Read a CSV, using chunked reads for files over the size threshold."""
    size = path.stat().st_size
    if size > _LARGE_FILE_THRESHOLD:
        logger.info("Large file detected (%d MB), reading in chunks: %s",
                     size // (1024 * 1024), path.name)
        chunks = pd.read_csv(path, encoding="utf-8", chunksize=100_000)
        return pd.concat(chunks, ignore_index=True)
    return pd.read_csv(path, encoding="utf-8")


def _parse_timestamps(df: pd.DataFrame, table_name: str) -> dict[str, int]:
    """Parse timestamp columns to UTC datetime in-place. Returns NaT counts."""
    nat_counts: dict[str, int] = {}
    for col in _TIMESTAMP_COLUMNS:
        if col not in df.columns:
            continue
        before_nat = df[col].isna().sum()
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        after_nat = df[col].isna().sum()
        new_nat = int(after_nat - before_nat)
        if new_nat > 0:
            logger.warning("%s.%s: %d rows failed timestamp parsing (NaT)",
                           table_name, col, new_nat)
        nat_counts[col] = new_nat
    return nat_counts


def _validate_schema(df: pd.DataFrame, table_name: str) -> list[str]:
    """Return list of expected columns missing from the DataFrame."""
    expected = set(EXPECTED_TABLES[table_name])
    actual = set(df.columns)
    return sorted(expected - actual)


def list_available_repos() -> list[str]:
    """Return repo slugs that have all 7 expected shard files present."""
    if not SHARDS_DIR.exists():
        return []
    available = []
    for entry in sorted(SHARDS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        files = {f.stem for f in entry.glob("*.csv")}
        if set(EXPECTED_TABLES.keys()).issubset(files):
            available.append(entry.name)
    return available


def validate_shards(repo_slug: str) -> ValidationReport:
    """Validate shard files for a repo without raising. Returns a report."""
    report = ValidationReport(repo_slug=repo_slug)
    shard_path = _shard_dir(repo_slug)

    if not shard_path.exists():
        report.tables_missing = list(EXPECTED_TABLES.keys())
        return report

    for table_name in EXPECTED_TABLES:
        csv_path = shard_path / f"{table_name}.csv"
        if not csv_path.exists():
            report.tables_missing.append(table_name)
            continue

        report.tables_present.append(table_name)
        df = _read_csv(csv_path)
        report.row_counts[table_name] = len(df)

        missing_cols = _validate_schema(df, table_name)
        if missing_cols:
            report.schema_mismatches[table_name] = missing_cols

        nat_counts = _parse_timestamps(df, table_name)
        if any(v > 0 for v in nat_counts.values()):
            report.timestamp_nat_counts[table_name] = {
                k: v for k, v in nat_counts.items() if v > 0
            }

    report.is_valid = (
        len(report.tables_missing) == 0
        and len(report.schema_mismatches) == 0
    )
    return report


def load_repo(repo_slug: str) -> RepoDataset:
    """Load all 7 shard tables for a repo into a RepoDataset.

    Raises:
        ShardNotFoundError: if the shard directory or a CSV file is missing.
        SchemaMismatchError: if any CSV is missing expected columns.
    """
    shard_path = _shard_dir(repo_slug)
    if not shard_path.exists():
        raise ShardNotFoundError(
            f"Shard directory not found: {shard_path}\n"
            f"Run: bash scripts/pull_shards.sh"
        )

    frames: dict[str, pd.DataFrame] = {}
    all_missing_cols: dict[str, list[str]] = {}

    for table_name in EXPECTED_TABLES:
        csv_path = shard_path / f"{table_name}.csv"
        if not csv_path.exists():
            raise ShardNotFoundError(
                f"Missing shard file: {csv_path}\n"
                f"Run: bash scripts/pull_shards.sh"
            )

        df = _read_csv(csv_path)
        missing_cols = _validate_schema(df, table_name)
        if missing_cols:
            all_missing_cols[table_name] = missing_cols

        _parse_timestamps(df, table_name)
        frames[table_name] = df

    if all_missing_cols:
        details = "; ".join(
            f"{t}: missing {cols}" for t, cols in all_missing_cols.items()
        )
        raise SchemaMismatchError(f"Schema validation failed — {details}")

    return RepoDataset(
        repo_meta=frames["repos"],
        developers=frames["developers"],
        commits=frames["commits"],
        files=frames["files"],
        commit_parents=frames["commit_parents"],
        developer_edges=frames["developer_commit_edges"],
        file_edges=frames["commit_file_edges"],
    )
