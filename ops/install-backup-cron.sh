#!/bin/bash
# Installs the daily backup cron job. Idempotent -- safe to re-run.
set -euo pipefail

SCRIPT_PATH="/var/www/vaani-backend/ops/backup.sh"
CRON_LINE="0 2 * * * $SCRIPT_PATH >> /var/backups/vaani/cron.log 2>&1"

chmod +x "$SCRIPT_PATH"

( crontab -l 2>/dev/null | grep -vF "$SCRIPT_PATH" || true ; echo "$CRON_LINE" ) | crontab -

echo "Installed cron job:"
crontab -l | grep -F "$SCRIPT_PATH"
