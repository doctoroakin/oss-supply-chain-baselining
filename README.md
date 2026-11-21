# Heron Fellowship: Behavioral Baselining

Behavioral baselining for OSS supply chain attack detection. Apart Research / Heron fellowship work.

My part of a five-person team project in the Heron AI Security Fellowship (2026). We split the work evenly. This repo holds what I built: the data loader and the evaluation harness, with 5 known malicious commits, 15 benign contributors and a mock detector. The architecture overview describes the plan for the whole system. There's no real detector in this repo and no results.

---

## Threat Models

We target three supply chain attack patterns:

- **Impersonation**: git infrastructure or credentials compromised, commits pushed under a real maintainer's identity (PHP 2021, LiteLLM 2026).
- **Cold-start takeover**: brand new contributor handed access, immediately ships malicious code (event-stream 2018).
- **Patient attacker**: years of legitimate contributions build trust, then payload (XZ Utils 2024).

## Quick Start

```bash
# Clone the repo
git clone https://github.com/doctoroakin/oss-supply-chain-baselining.git
cd oss-supply-chain-baselining

# Install requirements
pip install -r requirements.txt

# Set environment variables (these point to the research box and data root)
export REMOTE_HOST="user@your-research-host"
export SSH_KEY="$HOME/.ssh/your_key"
export REMOTE_ROOT="/path/to/data/root"

# Fetch repo data locally
bash scripts/pull_shards.sh

# Verify the harness works
python -m heron.harness run --detector mock --threshold 0.5
```

## Documentation

- `specs/architecture-overview.md`: full architecture and design principles
- `specs/spec-001-data-loader.md` and `specs/spec-002-evaluation-harness.md`: completed build specs

## Data

`data/` and `output/` are gitignored. Repo data is reproducible from the data loader given access to the research box.
