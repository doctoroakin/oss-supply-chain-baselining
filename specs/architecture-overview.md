# Heron Fellowship — Architecture Overview

**Status:** v1
---

## Three Threat Models

"OSS supply chain attack" is not one problem. It is three problems wearing the same label, and each needs a different detection approach.

**1. Impersonation** (php-src case — two impersonation commits)
Git infrastructure compromised, commits pushed under real maintainers' names. The legitimate humans never touched a keyboard. **Detection signal:** the claimed author's commit doesn't match their established baseline. We use the legitimate contributor's deep history as a lie detector against the impersonator.

**2. Cold-start takeover** (event-stream case — the new contributor's commit)
Brand-new contributor, days of activity, gets handed access, immediately malicious. **Detection signal:** not deviation from individual baseline (there isn't one yet) — instead, peer-relative trust scoring. How fast did they earn access? What are they touching relative to their tenure? Are they an outlier among contributors with similar history?

**3. Patient attacker** (XZ Utils case — the backdoor commits)
Years of legitimate contributions, builds real trust, becomes a maintainer, then deploys. By the time the attack lands, the attacker IS the baseline. **Detection signal:** privilege escalation arc over a long horizon. The attacker can fake timing, scope, and message style indefinitely, but cannot fake the trajectory from "docs contributor" to "build pipeline modifier" — that drift across security-critical territory is the signal.

Each threat model shares the same enrichment pipeline but uses a different detector tuned to its pattern.

## Architecture (4 layers)

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: TELEMETRY                                          │
│ Git history (PyDriller CSVs) + GH Archive events            │
│ Today: historical replay. Future: live GitHub webhooks.     │
│ Same downstream pipeline either way.                        │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│ Layer 2: ENRICHMENT (deterministic feature extraction)      │
│ Per-commit: temporal, scope, complexity, semantic,          │
│ privilege, identity features.                               │
│ Schema mirrors the security-domain pattern:                 │
│   commit_info.* (what happened — raw facts)                 │
│   enrichment.* (what we inferred — features and scores)     │
│ Versioned (processor_version), affects_* tagging for        │
│ cross-domain queries.                                       │
│                                                             │
│ ⚡ LLM SLOT 1: Identity Resolution                          │
│ Deterministic matching on email/login/normalized name      │
│ handles 80%. LLM adjudicates residual hard cases to        │
│ produce a stable canonical_contributor_id.                 │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│ Layer 3: DETECTION (deterministic inference)                │
│                                                             │
│  3a. Per-Commit Detector                                    │
│      Single enriched commit → {confidence, evidence[],     │
│      signals[]}. Catches impersonation and cold-start.     │
│                                                             │
│  3b. Sequence Pattern Detector                              │
│      Per-contributor history → phase progression analysis. │
│      YAML-defined phase signatures. Catches patient        │
│      attacker (XZ pattern).                                │
│                                                             │
│  3c. Cross-Actor Correlation Detector (Phase 2)             │
│      Detects correlated baseline shifts across multiple    │
│      actors. Catches sock-puppet coordination.             │
│                                                             │
│ All three return the same structured evidence shape.       │
│ All three are tuned against the labeled evaluation harness. │
│                                                             │
│ ⚡ LLM SLOT 2: Phase Signature Bootstrapping                │
│ LLM proposes candidate phase signatures from example       │
│ trajectories. Humans review. Output encoded in YAML.       │
│ LLM does discovery, deterministic engine does enforcement. │
└────────────────────┬────────────────────────────────────────┘
                     │
                     │ (only flagged cases proceed)
                     │
┌────────────────────▼────────────────────────────────────────┐
│ Layer 4: LLM CONTEXTUAL ANALYSIS                            │
│ Structured evidence in, narrative explanation out.         │
│ Cross-signal synthesis: "what story do these signals       │
│ tell together?" Risk assessment, recommended action.       │
│                                                             │
│ ⚡ LLM SLOT 3: Sock-Puppet Adjudication                     │
│ For correlation candidates from 3c, LLM evaluates          │
│ whether flagged actors look like coordinated humans or     │
│ sock-puppets of the same human.                            │
└─────────────────────────────────────────────────────────────┘
```

## Schema Decisions

**Two-namespace split per commit record.**
- `commit_info.*` — what happened. Author, timestamp, files touched, lines changed, message. Raw facts.
- `enrichment.*` — what we inferred. Computed features, scores, tags. Never confused with the raw facts.

**affects_* boolean tagging for cross-domain queries.**
- `affects_build`, `affects_release`, `affects_crypto`, `affects_dependencies`, `affects_signing`, `affects_ci`, `affects_tests`, `affects_docs`
- Makes phase queries trivial: "find contributors where affects_docs=true commits preceded affects_build=true commits."

**Cold-start as first-class fields, not inferred from absence.**
- `enrichment.contributor_age_days` — days since first commit in this repo
- `enrichment.commits_observed_in_repo` — total prior commits in this repo
- `enrichment.first_seen_at_repo` — ISO timestamp of first commit
- `enrichment.repo_age_at_contributor_start` — context for whether the contributor showed up to a mature project or a fresh one

**Structured evidence from day one.**
Every detection function returns:
```python
{
  "confidence": 0.78,
  "evidence": [
    {
      "tag": "off_hours_commit",
      "weight": 0.15,
      "fields": {"hour_utc": 3, "contributor_typical_hours": [9, 17]},
      "prose": "Commit at 03:00 UTC, contributor typically commits 09:00–17:00"
    },
    ...
  ],
  "signals_evaluated": ["temporal", "scope", "privilege", "semantic"],
  "detector_version": "1.0"
}
```
- `tag` — stable enum the LLM and downstream code key off
- `weight` — audit trail for how confidence was built
- `fields` — structured context for the LLM, no re-parsing needed
- `prose` — human-readable for the alert UI

**Versioned schemas.**
- `processor_version` on every enriched commit
- `detector_version` on every detection result
- Sentinel `None` for absent fields, never `0.0` (distinguishes field-absent from field-present-and-low)
- Rules check `processor_version >= N` before scoring on fields added in version N

**Pattern definitions as data, not code.**
- Phase signatures will live in YAML files under `patterns/` (not built yet)
- The sequence detector is the only Python that reads them
- Non-engineers can contribute new patterns without touching code

## LLM Integration Points

The deterministic layer is the spine. LLMs sit at three specific slots where judgment beats rules:

1. **Identity Resolution** — disambiguate hard cases after deterministic matching has handled the easy ones. One developer, many emails over years, is the canonical hard case for our domain.

2. **Phase Signature Bootstrapping** — propose candidate phase signatures from labeled trajectories. We hand the LLM the XZ attacker's commit history and ask "what behavioral phases do you see?" Output is reviewed and encoded in YAML.

3. **Contextual Analysis & Sock-Puppet Adjudication** — narrative explanation of flagged cases, plus coordinated-actor evaluation when the cross-actor detector surfaces candidates.

What LLMs do NOT do: enrichment, per-commit scoring, threshold decisions, or anything in the per-event hot path. Cheap math first, expensive judgment second, only on flagged cases.

## Spec Sequence

The labeled evaluation harness ships before any detection code, so that every threshold and weight decision is grounded in measured performance rather than intuition.

| Spec | What it ships | Why this order |
|------|---------------|----------------|
| 001 ✅ | Data Loader | Foundation. Pulls and validates raw shards. Done. |
| 002 | **Labeled Evaluation Harness** | Every threshold decision references this. Build it first. |
| 003 | Identity Resolution Layer | Produces canonical_contributor_id for everything downstream. |
| 004 | Commit Enrichment Layer | Pure deterministic feature extraction. The "enrichment" namespace. |
| 005 | Per-Commit Inference Detector | First detection layer. Tuned against harness from 002. Validates impersonation + cold-start. |
| 006 | Sequence Pattern Detector | Phase progression analysis. Validates patient attacker (XZ). |
| 007 | Cross-Actor Correlation Detector | Phase 2. Sock-puppet detection. Designed-in from day one but built last. |

## Design Principles

1. **Investigate before implementation.** Every spec is preceded by an investigation phase. Spec ambiguity is a bug in the spec, not a license to improvise.
2. **Cheap deterministic math first, expensive LLM reasoning second.** LLMs only see flagged cases.
3. **Multi-signal confidence over single-signal triggers.** No single signal fires an alert.
4. **Structured evidence, never opaque scores.** Every detection result is auditable and parseable.
5. **Versioned everything.** Schemas, detectors, patterns. Field-absent must be distinguishable from field-present-and-low.
6. **Density beats length.** Window selection is per-contributor, not global. Pick the window where the contributor is dense, not where it's longest.
7. **Domain-normalize at the collector layer.** Don't let GitHub-isms leak into rules. Forge, GitLab, mailing list — all become trivial later if we normalize early.
8. **Pattern definitions as data.** YAML, not Python.
9. **Labeled harness drives tuning, not vibes.** Use available ground truth to drive tuning. Every threshold decision references the evaluation harness. Ground truth positives and healthy-contributor baselines make intuition-based tuning unnecessary.
10. **Build for the eventual real-time product.** Same engine, different fuel line. Don't take shortcuts that lock us into batch-mode.
11. **LLMs propose, deterministic code disposes.** LLMs are inputs to decisions, never the decision itself. Identity links, phase memberships, alert firings, and pattern definitions are all committed by deterministic code paths that take LLM output as one signal among several. When an LLM hallucinates — and one will — the blast radius must be bounded and the failure must be debuggable. This also makes the system auditable to security buyers, which matters more than it sounds.
12. **Extend the YAML vocabulary, never puncture it.** When a phase pattern wants something the schema doesn't support, the right move is to add new node types with documentation and schema versioning. The wrong move is `eval:` or `python_callback:` or any other escape hatch that lets code leak into config. Every escape hatch we leave, we regret within a quarter. The pattern analyzer is the only Python that interprets patterns.

## Validation Strategy


**Positive scenarios (we know these are bad):**
- php-src: first impersonation commit (cf44e4b...)
- php-src: second impersonation commit (2b0f239...)
- event-stream: cold-start takeover (e316336...)
- XZ Utils: phase 1 backdoor (cf44e4b...)
- XZ Utils: phase 2 backdoor (6e63681...)

**Negative scenarios (we know these are good):**
- 5–20 well-known long-tenured maintainers from the same repos, with their full commit histories
- Selected to span: high-frequency vs low-frequency, single-area vs multi-area, solo vs collaborative

**Metrics:**
- Recall on positives at fixed alert burden
- False positive rate on negatives
- Precision-recall curve across threshold settings
- Per-threat-model breakdown (does the system catch impersonation? cold-start? patient?)

