#!/usr/bin/env bash
set -euo pipefail

# Pull per-repo PyDriller shards from the research box to local data/shards/.
# Usage: bash scripts/pull_shards.sh [--force]
#
# Required environment variables:
#   REMOTE_HOST  — user@host for the research box (e.g. ubuntu@203.0.113.10)
#   SSH_KEY      — path to the SSH private key for REMOTE_HOST
#   REMOTE_ROOT  — absolute path to the shards directory on REMOTE_HOST

REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST environment variable}"
SSH_KEY="${SSH_KEY:?Set SSH_KEY environment variable}"
REMOTE_ROOT="${REMOTE_ROOT:?Set REMOTE_ROOT environment variable}"
LOCAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)/data/shards"

# Repo pairs: "local_slug:remote_slug"
REPOS="php-src:php__php-src event-stream:dominictarr__event-stream systemd:gentoo__systemd"

TABLES="repos developers commits files commit_parents developer_commit_edges commit_file_edges"

FORCE=false
if [[ "${1:-}" == "--force" ]]; then
    FORCE=true
fi

SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=accept-new"
pulled=0
skipped=0
failed=0
total_bytes=0
start_time=$(date +%s)

for pair in $REPOS; do
    local_slug="${pair%%:*}"
    remote_slug="${pair##*:}"
    local_dir="$LOCAL_ROOT/$local_slug"
    mkdir -p "$local_dir"

    for table in $TABLES; do
        remote_file="$REMOTE_ROOT/$table/$remote_slug.csv"
        local_file="$local_dir/$table.csv"

        if [[ "$FORCE" == false && -f "$local_file" ]]; then
            # Compare sizes to decide whether to skip
            local_size=$(stat -f%z "$local_file" 2>/dev/null || stat -c%s "$local_file" 2>/dev/null)
            remote_size=$(ssh $SSH_OPTS "$REMOTE_HOST" "stat -c%s '$remote_file'" 2>/dev/null || echo "0")

            if [[ "$local_size" == "$remote_size" && "$local_size" -gt 0 ]]; then
                echo "  SKIP  $local_slug/$table.csv (${local_size} bytes, size matches)"
                skipped=$((skipped + 1))
                continue
            fi
        fi

        echo "  PULL  $local_slug/$table.csv"
        if scp $SSH_OPTS "$REMOTE_HOST:$remote_file" "$local_file"; then
            file_size=$(stat -f%z "$local_file" 2>/dev/null || stat -c%s "$local_file" 2>/dev/null)
            total_bytes=$((total_bytes + file_size))
            pulled=$((pulled + 1))
        else
            echo "  FAIL  $local_slug/$table.csv" >&2
            failed=$((failed + 1))
        fi
    done
done

end_time=$(date +%s)
elapsed=$((end_time - start_time))

echo ""
echo "=== Summary ==="
echo "  Pulled:  $pulled files"
echo "  Skipped: $skipped files (already present with matching size)"
echo "  Failed:  $failed files"
echo "  Bytes:   $total_bytes ($(echo "scale=1; $total_bytes / 1048576" | bc) MB)"
echo "  Time:    ${elapsed}s"

if [[ $failed -gt 0 ]]; then
    echo "ERROR: $failed file(s) failed to download." >&2
    exit 1
fi
