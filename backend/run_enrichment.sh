#!/usr/bin/env bash
#
# Scheduled enrichment run. Invoked from cron; safe to run by hand.
#
#   ./run_enrichment.sh          # process everything pending
#   ./run_enrichment.sh --limit 5
#
# Install (on the VM):
#   chmod +x ~/paperswap/backend/run_enrichment.sh
#   mkdir -p ~/paperswap/logs
#   crontab -e
#     0 */6 * * * /home/ubuntu/paperswap/backend/run_enrichment.sh >> /home/ubuntu/paperswap/logs/enrich.log 2>&1
#
# Every 6 hours rather than nightly. Measured cost is ~3.4 s/article, so a full
# 84-article batch takes ~5 minutes, and the job is a no-op when nothing is
# pending (it selects WHERE enriched_at IS NULL and exits). Articles arrive on a
# 12-hour refresh, so running 4x/day means a card back is never empty for more
# than a few hours, at a cost of about 20 minutes of CPU per day.
#
# (ADR-011 describes this as a nightly job. The reasoning is unchanged -- one
# shot, separate process, exits when done -- but the cadence in that document is
# now out of date.)

set -euo pipefail

# cron runs with a minimal PATH that usually lacks docker. readlink -f resolves
# symlinks so the script works when invoked through one.
export PATH=/usr/local/bin:/usr/bin:/bin
cd "$(dirname "$(readlink -f "$0")")"

# Self-locking rather than relying on `flock` in the crontab line. A manual run
# started while cron's run is still going would otherwise put two ONNX sessions
# on a 956 MB box at once -- roughly 300 MB of avoidable pressure, on the
# machine where the OOM killer's favourite target is Postgres.
#
# -n means fail immediately instead of queueing: a skipped run costs nothing,
# since the next one picks up the same pending rows.
exec 9>/tmp/paperswap-enrich.lock
if ! flock -n 9; then
    echo "$(date -Is) [skip] another enrichment run holds the lock"
    exit 0
fi

echo "$(date -Is) [start] enrichment"

# No --build. The enrichment service shares paperswap-backend:latest with the
# API, which `docker compose up -d --build` refreshes on deploy. Building here
# would rebuild on every cron tick for no reason.
#
# If this fails with "image not found", the deploy step was skipped -- run
# `docker compose up -d --build` once.
docker compose run --rm enrichment python enrichment.py "$@"

echo "$(date -Is) [done] enrichment"
