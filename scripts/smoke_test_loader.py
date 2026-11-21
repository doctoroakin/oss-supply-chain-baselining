#!/usr/bin/env python3
"""Smoke test for the data loader (Spec 001).

Loads both repos, validates schemas, checks row counts,
and confirms the 3 known malicious commit SHAs are present.
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heron.data_loader import load_repo, list_available_repos, validate_shards

# Ground truth: known malicious commit SHAs (also listed in harness/scenarios/positives.yaml)
MALICIOUS_COMMITS = {
    "php-src": [
        "c730aa26bd52829a49f2ad284b181b7e82a68d7d",  # PHP backdoor (CWE-510)
        "2b0f239b211c7544ebc7a4cd2c977a5b7a11ed8a",  # PHP backdoor (CWE-510)
    ],
    "event-stream": [
        "e3163361fed01384c986b9b4c18feb1fc42b8285",  # event-stream backdoor (CWE-912)
    ],
}


def main() -> int:
    errors: list[str] = []

    # 1. Check available repos
    available = list_available_repos()
    print(f"Available repos: {available}")
    for slug in ("php-src", "event-stream"):
        if slug not in available:
            errors.append(f"Repo '{slug}' not in available repos")

    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        return 1

    # 2. Validate shards
    for slug in ("php-src", "event-stream"):
        report = validate_shards(slug)
        print(f"\n{'='*60}")
        print(f"Validation: {slug}")
        print(f"  Valid:   {report.is_valid}")
        print(f"  Tables:  {report.tables_present}")
        if report.tables_missing:
            errors.append(f"{slug}: missing tables {report.tables_missing}")
        if report.schema_mismatches:
            errors.append(f"{slug}: schema mismatches {report.schema_mismatches}")
        if report.timestamp_nat_counts:
            print(f"  NaT counts: {report.timestamp_nat_counts}")
        print(f"  Row counts:")
        for table, count in sorted(report.row_counts.items()):
            print(f"    {table:30s} {count:>10,}")

    # 3. Load both repos and inspect
    for slug in ("php-src", "event-stream"):
        print(f"\n{'='*60}")
        print(f"Loading: {slug}")
        try:
            repo = load_repo(slug)
        except Exception as exc:
            errors.append(f"{slug}: load failed — {exc}")
            continue

        print(f"  commits:    {len(repo.commits):>10,} rows")
        print(f"  developers: {len(repo.developers):>10,} rows")
        print(f"  files:      {len(repo.files):>10,} rows")

        if len(repo.commits) == 0:
            errors.append(f"{slug}: commits table is empty")

        # Print first 3 commits
        print(f"\n  First 3 commits:")
        cols = ["commit_id", "author_name", "authored_at_utc", "message"]
        display_cols = [c for c in cols if c in repo.commits.columns]
        for _, row in repo.commits.head(3).iterrows():
            msg = str(row.get("message", ""))[:60]
            print(f"    {row['commit_id'][:12]}  {str(row.get('author_name','')):20s}  {msg}")

        # 4. Check timestamp parsing quality
        for col in ("authored_at_utc", "committed_at_utc"):
            if col in repo.commits.columns:
                nat_count = repo.commits[col].isna().sum()
                total = len(repo.commits)
                pct = (nat_count / total * 100) if total > 0 else 0
                if pct >= 1.0:
                    errors.append(f"{slug}: {col} has {pct:.1f}% NaT ({nat_count}/{total})")
                print(f"  {col}: {nat_count} NaT / {total} total ({pct:.2f}%)")

    # 5. Verify malicious commits exist
    print(f"\n{'='*60}")
    print("Malicious commit verification:")
    for slug, shas in MALICIOUS_COMMITS.items():
        repo = load_repo(slug)
        commit_ids = set(repo.commits["commit_id"])
        for sha in shas:
            found = sha in commit_ids
            status = "FOUND" if found else "MISSING"
            print(f"  [{status}] {slug}: {sha[:12]}...")
            if not found:
                errors.append(f"{slug}: malicious commit {sha} not found in commits table")

    # Summary
    print(f"\n{'='*60}")
    if errors:
        print(f"SMOKE TEST FAILED ({len(errors)} error(s)):")
        for e in errors:
            print(f"  - {e}")
        return 1
    else:
        print("SMOKE TEST PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
