# Spec 001: Data Loader & Shard Puller

**Layer:** Telemetry (feeds all downstream Baselines, Detection, LLM layers)
**Status:** Draft v1
---

## Objective
Pull per-repo PyDriller shards for `php-src` and `event-stream` from the Lambda box to local disk, validate schema integrity, and expose a clean Python API that returns trustworthy pandas DataFrames for any downstream component.

## Context
This is the foundation. Every other spec — baseline computation, deviation detection, LLM analysis — depends on getting clean data into memory reliably. We are deliberately scoping this small: two repos only, no transformation, no analysis. The goal is a boring, trustworthy data access layer that the rest of the project can build on without thinking about it.

## Inputs

### Remote source
- **Host:** `$REMOTE_HOST`
- **SSH key:** `$SSH_KEY`
- **Data root:** `$REMOTE_ROOT`
- **Shards path:** `shards/<table>/<repo_slug>.csv`

### Repos in scope
- `php-src`
- `event-stream`

### Tables to pull (7 per repo = 14 files total)
1. `repos.csv`
2. `developers.csv`
3. `commits.csv`
4. `files.csv`
5. `commit_parents.csv`
6. `developer_commit_edges.csv`
7. `commit_file_edges.csv`

### Expected schemas
See `heron/data_loader.py` for the canonical schema definitions — the loader validates expected columns at load time.

## Outputs

### Local file layout
```
oss-supply-chain-baselining/
├── data/
│   └── shards/
│       ├── php-src/
│       │   ├── repos.csv
│       │   ├── developers.csv
│       │   ├── commits.csv
│       │   ├── files.csv
│       │   ├── commit_parents.csv
│       │   ├── developer_commit_edges.csv
│       │   └── commit_file_edges.csv
│       └── event-stream/
│           └── (same 7 files)
```

### Python API
A module `heron/data_loader.py` exposing:

```python
from heron.data_loader import load_repo

repo = load_repo("php-src")
# Returns a RepoDataset object with attributes:
#   repo.commits           -> pd.DataFrame
#   repo.developers        -> pd.DataFrame
#   repo.files             -> pd.DataFrame
#   repo.commit_parents    -> pd.DataFrame
#   repo.developer_edges   -> pd.DataFrame  (developer_commit_edges)
#   repo.file_edges        -> pd.DataFrame  (commit_file_edges)
#   repo.repo_meta         -> pd.DataFrame  (single-row repos.csv)
```

Plus convenience helpers:
```python
from heron.data_loader import list_available_repos, validate_shards

list_available_repos()       # -> ["php-src", "event-stream"]
validate_shards("php-src")   # -> ValidationReport (see below)
```

### Validation report shape
```python
@dataclass
class ValidationReport:
    repo_slug: str
    tables_present: list[str]
    tables_missing: list[str]
    schema_mismatches: dict[str, list[str]]   # table -> missing columns
    row_counts: dict[str, int]                # table -> row count
    sha256_checks_passed: bool                # if checksums available
    is_valid: bool                            # True iff all checks pass
```

## Logic

### Step 1: Sync script (`scripts/pull_shards.sh`)
- Bash script that uses `scp` to pull all 14 files from the Lambda box to `data/shards/<repo>/`.
- Idempotent: skip files that already exist locally with matching size unless `--force` flag passed.
- Print a clear summary: how many files pulled, total bytes, time elapsed.
- Exit non-zero on any failure with a clear error message.

### Step 2: Loader module (`heron/data_loader.py`)
- `load_repo(repo_slug: str) -> RepoDataset`:
  1. Verify the repo's shard directory exists locally. If not, raise `ShardNotFoundError` with a hint to run `pull_shards.sh`.
  2. Load each of the 7 CSVs into a pandas DataFrame.
  3. Run schema validation against expected columns. Raise `SchemaMismatchError` listing any missing columns.
  4. Parse timestamp columns to pandas datetime with UTC timezone.
  5. Return a `RepoDataset` dataclass.

- `validate_shards(repo_slug: str) -> ValidationReport`:
  - Same checks as `load_repo` but returns a report instead of raising. For pre-flight validation in scripts and notebooks.

- `list_available_repos() -> list[str]`:
  - Scan `data/shards/` and return slugs that have all 7 expected files present.

### Step 3: Smoke test script (`scripts/smoke_test_loader.py`)
- Loads both repos.
- Prints row counts for each table.
- Prints first 3 rows of `commits.csv` for each repo.
- Verifies expected ground-truth presence: confirms the 3 known malicious commit SHAs exist in the loaded `commits` DataFrames (2 in php-src, 1 in event-stream; the SHAs are listed in `harness/scenarios/positives.yaml`).
- Exits 0 on success, non-zero on any failure.

## Edge cases

- **Missing shard file:** Raise `ShardNotFoundError`, do not silently return empty DataFrame.
- **Schema drift:** If a column is missing or renamed, raise `SchemaMismatchError` listing the exact diff. Never coerce or guess.
- **Timestamp parsing failures:** Log row index and raw value, continue parsing the rest, mark the row's timestamp as `NaT`. Surface count of failed rows in the validation report.
- **Empty CSV file (header only):** Valid state, return empty DataFrame with correct columns. Some control repos may legitimately have zero rows in some tables.
- **Unicode in commit messages / author names:** Handle UTF-8 explicitly. Do not silently drop bad bytes.
- **Very large `commit_file_edges.csv`:** Load lazily if file exceeds 500 MB (use chunked read or polars fallback). For php-src and event-stream this should not trigger but the check should be in place.

## Validation (how we know it worked)

Run the smoke test:
```bash
python3 scripts/smoke_test_loader.py
```

**Pass criteria:**
1. Both repos load without errors.
2. All 7 tables present for each repo.
3. Schema validation passes for all 14 files.
4. `commits` table for php-src has > 0 rows. Same for event-stream.
5. Timestamp columns parse to UTC datetime without bulk failures (< 1% of rows as `NaT`).
6. The 3 known malicious commit SHAs are findable in the loaded data.
7. Smoke test exits 0.

## Out of scope

This spec deliberately does NOT include:
- Any baseline computation or feature engineering
- Any deviation detection or scoring
- Any LLM calls
- Any GH Archive data loading (separate spec later)
- Any loading of repos beyond php-src and event-stream
- Any data transformation, normalization, or cleaning beyond timestamp parsing
- Any caching or performance optimization beyond the size-based skip in the sync script
- Any tests beyond the smoke test (unit tests are a follow-up spec if needed)

## Deliverables checklist

- [ ] `scripts/pull_shards.sh` — bash sync script
- [ ] `heron/__init__.py` — empty package init
- [ ] `heron/data_loader.py` — loader module with `load_repo`, `validate_shards`, `list_available_repos`
- [ ] `heron/exceptions.py` — `ShardNotFoundError`, `SchemaMismatchError`
- [ ] `scripts/smoke_test_loader.py` — smoke test
- [ ] `data/shards/php-src/` — 7 CSV files pulled from Lambda
- [ ] `data/shards/event-stream/` — 7 CSV files pulled from Lambda
- [ ] Smoke test passes with exit 0
