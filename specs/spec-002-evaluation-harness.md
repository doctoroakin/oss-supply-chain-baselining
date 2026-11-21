# Spec 002: Labeled Evaluation Harness

**Layer:** Validation infrastructure (sits across all downstream Detection layers)
**Status:** Draft v1
**Depends on:** Spec 001 (Data Loader) ✅

---

## Objective
Build a one-command evaluation harness that runs labeled positive and negative scenarios through any current detector configuration and reports precision, recall, and per-threat-model breakdowns. This ships **before** any detection code so that every threshold and weight decision from here forward is grounded in measured performance, not intuition.

## Context
Building a labeled evaluation harness before any detection code is the single highest-leverage methodological choice in this work. Every threshold and weight decision from this point forward references this harness. Without it, tuning is intuition-driven and irreproducible. We have ground truth: three known malicious commits in the existing corpus, the XZ Utils backdoor commits we extracted ourselves, and a population of healthy long-tenured maintainers as negatives. The harness converts every architectural debate into a measured tradeoff.

This spec produces no detection logic. It produces the apparatus that will judge every detector we build from Spec 005 onward.

## Inputs

### Positive scenarios (known malicious)
| Repo | Commit SHA | Author | Threat model | Source |
|------|------------|--------|--------------|--------|
| php-src | `c730aa26bd52` | Core maintainer (impersonated) | Impersonation | External research corpus (not included) |
| php-src | `2b0f239b211c` | Core maintainer (impersonated) | Impersonation | External research corpus (not included) |
| event-stream | `e3163361fed0` | New contributor | Cold-start takeover | External research corpus (not included) |
| xz | `cf44e4b7f5df` | Long-tenured contributor | Patient attacker (phase 1) | TBD when XZ unblocks |
| xz | `6e636819e8f070330d835fce46289a3ff72a7b89` | Long-tenured contributor | Patient attacker (phase 2) | TBD when XZ unblocks |

XZ scenarios are stubbed in the harness from day one but marked `pending=True` until extraction completes. The harness skips pending scenarios with a clear log line, never fails because of them.

### Negative scenarios (known healthy)
A curated list of 10–20 long-tenured maintainer histories drawn from `php-src` and `event-stream`. Selection criteria:
- At least 50 commits in the repo
- Active over a span of at least 6 months
- Not present in any malicious commit author list
- Spans the variety axes: high-frequency vs low-frequency, single-area vs multi-area, solo vs collaborative

The exact selection is captured in `harness/scenarios/negatives.yaml` and reviewed by JV before harness runs are considered authoritative. Initial seed list: top 5 contributors by commit count from each repo, minus any whose accounts appear suspicious on inspection.

### Detector under test
The harness accepts a detector callable with the contract:
```python
def detector(enriched_commit: dict, context: dict) -> DetectionResult
```
Where `DetectionResult` is the structured shape:
```python
{
  "confidence": float,
  "evidence": list[dict],
  "signals_evaluated": list[str],
  "detector_version": str
}
```
For Spec 002, no real detector exists yet. The harness ships with a `MockDetector` that returns random scores so the harness itself can be tested end-to-end. Real detectors plug in from Spec 005 onward.

## Outputs

### CLI
```bash
python -m heron.harness run --detector mock --threshold 0.6
python -m heron.harness run --detector mock --threshold-sweep 0.3:0.9:0.05
python -m heron.harness run --detector mock --scenarios positives
python -m heron.harness run --detector mock --threat-model impersonation
```

### Reports
After every run, the harness writes:
```
output/harness/runs/<timestamp>/
├── summary.json         # headline metrics
├── per_scenario.csv     # one row per scenario, full result
├── confidence_log.csv   # every confidence score, even sub-threshold
└── pr_curve.json        # if --threshold-sweep used
```

`summary.json` shape:
```json
{
  "run_id": "2026-04-08T14:30:00Z",
  "detector": "mock",
  "detector_version": "0.0.1",
  "threshold": 0.6,
  "scenarios_run": 23,
  "scenarios_pending": 2,
  "positives": {
    "total": 3,
    "caught": 2,
    "missed": 1,
    "recall": 0.667
  },
  "negatives": {
    "total": 20,
    "false_positives": 1,
    "true_negatives": 19,
    "false_positive_rate": 0.05
  },
  "per_threat_model": {
    "impersonation": {"recall": 1.0, "fpr": 0.05, "n_pos": 2, "n_neg": 20},
    "cold_start": {"recall": 0.0, "fpr": 0.05, "n_pos": 1, "n_neg": 20},
    "patient_attacker": {"status": "pending", "n_pos": 2}
  }
}
```

### Console output
Human-readable summary printed to stdout, including:
- Headline recall and FPR
- Per-threat-model breakdown
- List of caught and missed positives by name
- Top 3 false positives by confidence (for review)
- Path to the run output directory

## Logic

### Step 1: Scenario loading (`heron/harness/scenarios.py`)
- Load positive scenarios from `harness/scenarios/positives.yaml`
- Load negative scenarios from `harness/scenarios/negatives.yaml`
- Each scenario specifies: `id`, `repo`, `target_commit_sha` (for positives) or `contributor_id` (for negatives), `threat_model` (positives only), `pending` (bool)
- Resolve scenarios against the data loader from Spec 001 — pull the relevant commit and contributor history into memory

### Step 2: Detector invocation (`heron/harness/runner.py`)
For each non-pending scenario:
1. Build a `context` dict containing the contributor's prior commit history in this repo (everything before the target commit's timestamp)
2. Build an `enriched_commit` dict for the target commit. **For Spec 002, this is just the raw commit_info fields plus an empty `enrichment={}` block** — the real enrichment layer ships in Spec 004. The MockDetector ignores enrichment and returns random scores. Real detectors plug in later.
3. Call the detector with `(enriched_commit, context)`
4. Capture the full DetectionResult plus wall-clock time
5. Compare result confidence against threshold to determine fired/not-fired

### Step 3: Metric computation (`heron/harness/metrics.py`)
- Recall: positives caught / total positives
- False positive rate: negatives that fired / total negatives
- Per-threat-model breakdown using the `threat_model` field from positive scenarios
- Threshold sweep: re-run the same detector outputs against multiple threshold values (no detector re-execution needed — just re-bucket the existing scores)

### Step 4: Report writing (`heron/harness/reports.py`)
- Write all four output files described above
- Print human summary to stdout
- Exit 0 if run completed successfully (regardless of how many positives were caught — that's the detector's problem, not the harness's)
- Exit non-zero only on infrastructure failure (data loader error, scenario file malformed, etc.)

### Step 5: MockDetector for harness self-test (`heron/harness/mock_detector.py`)
- Returns deterministic random scores keyed off the commit SHA so runs are reproducible
- Implements the full DetectionResult contract correctly (real evidence shape, version string, etc.)
- Used to validate the harness itself works end-to-end before any real detector exists

## Edge cases

- **Pending scenarios (XZ before extraction):** Skip with log line, count separately in summary, never fail the run.
- **Contributor with zero prior history:** Pass empty context to detector. Detector is responsible for handling cold-start gracefully (cold-start IS one of our threat models).
- **Detector raises an exception on a scenario:** Catch, log, mark scenario as `error` in the report (distinct from `caught` or `missed`), continue with remaining scenarios. One bad scenario does not abort the run.
- **Negative scenario contributor turns out to also have malicious commits:** This should never happen if the curation is right, but the harness validates on load that no negative scenario's contributor authored any positive scenario's commit. Hard fail with clear error if detected.
- **Same SHA appears in two scenarios:** Hard fail on load with clear error.
- **Scenario file malformed (missing required field):** Hard fail on load with clear error pointing at the offending file and line.
- **Threshold sweep with no positives in scenario set:** Allowed, but PR curve will be degenerate. Print warning, write the curve anyway.

## Validation (how we know it worked)

Run the harness against the MockDetector:
```bash
python -m heron.harness run --detector mock --threshold 0.5
```

**Pass criteria:**
1. All non-pending scenarios run without errors
2. Summary report is written with correct shape
3. Per-scenario CSV has one row per scenario
4. Confidence log has one row per scenario regardless of fired/not-fired status
5. Per-threat-model breakdown is present and uses the right scenario counts
6. Pending scenarios (XZ) are listed as pending, not as missed
7. Console output is human-readable and points at the output directory
8. Exit code is 0
9. Re-running with the same threshold produces identical results (MockDetector is deterministic)

Then run the threshold sweep:
```bash
python -m heron.harness run --detector mock --threshold-sweep 0.0:1.0:0.1
```

**Additional pass criteria:**
10. PR curve JSON is written with one entry per threshold step
11. The sweep does not re-invoke the detector — it re-buckets cached scores
12. Console output shows the precision/recall tradeoff at each threshold

## Out of scope

This spec deliberately does NOT include:
- Any real detection logic (Spec 005)
- Any feature extraction or enrichment (Spec 004)
- Any identity resolution (Spec 003)
- Any LLM calls
- Any visualization beyond CSV/JSON output (we'll add plots later if needed, plain CSVs are enough for v1)
- Any database — everything is files on disk
- Any concurrency — sequential scenario execution is fine for our scale
- Any web UI or notebook interface
- Any auto-tuning or weight optimization — the harness measures, humans tune
- Any scenarios beyond what's listed in the inputs section

## Deliverables checklist

- [ ] `heron/harness/__init__.py`
- [ ] `heron/harness/scenarios.py` — scenario loader and validator
- [ ] `heron/harness/runner.py` — orchestration loop
- [ ] `heron/harness/metrics.py` — recall, FPR, threshold sweep math
- [ ] `heron/harness/reports.py` — CSV / JSON writers and console output
- [ ] `heron/harness/mock_detector.py` — harness self-test detector
- [ ] `heron/harness/__main__.py` — CLI entry point for `python -m heron.harness`
- [ ] `harness/scenarios/positives.yaml` — labeled positive scenarios (3 active, 2 pending)
- [ ] `harness/scenarios/negatives.yaml` — 10–20 healthy maintainer scenarios (review before authoritative runs)
- [ ] `output/harness/` — created on first run (gitignored)
- [ ] Smoke test: `python -m heron.harness run --detector mock --threshold 0.5` exits 0 with valid reports
- [ ] Smoke test: `python -m heron.harness run --detector mock --threshold-sweep 0.0:1.0:0.1` exits 0 with valid PR curve

## Why this ships before detection code

The principle: build the labeled evaluation harness before any rule, any threshold, or any alert template. Even with a small initial set of positive and negative scenarios, runnable with one command and scoring precision and recall, the harness converts every subsequent threshold decision from intuition into measurement. We have the ground truth to do this. We use it.
