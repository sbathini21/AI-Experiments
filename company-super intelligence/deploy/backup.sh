#!/usr/bin/env bash
# Nightly Postgres backup (cron: 15 2 * * * /opt/company-intel/deploy/backup.sh). Keeps 14 days locally.
# Copy offsite with rclone/restic to Backblaze B2 or similar for real durability.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
docker compose exec -T db pg_dump -U companyintel -Fc companyintel > "backups/db-$(date +%F).dump"
find backups -name 'db-*.dump' -mtime +14 -delete
