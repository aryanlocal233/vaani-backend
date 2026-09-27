#!/bin/bash
# Automated daily backup for Vaani: Postgres dump (FAQ packs, caches, analytics, devices) +
# the synthesized audio cache (represents real Sarvam AI spend -- regenerable, but not free to
# lose). Rotates local copies after RETENTION_DAYS. Run via cron (see ops/install-backup-cron.sh).
set -euo pipefail

BACKUP_DIR="/var/backups/vaani"
RETENTION_DAYS=14
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

log "Starting backup $TIMESTAMP"

# --- Postgres dump ---
DUMP_FILE="$BACKUP_DIR/vaani_db_${TIMESTAMP}.sql.gz"
if docker exec vaani-postgres pg_dump -U vaani vaani | gzip > "$DUMP_FILE"; then
    DUMP_SIZE=$(du -h "$DUMP_FILE" | cut -f1)
    log "Postgres dump OK: $DUMP_FILE ($DUMP_SIZE)"
else
    log "ERROR: pg_dump failed"
    rm -f "$DUMP_FILE"
    exit 1
fi

# --- Audio cache archive (full copy each run -- cache is small today; revisit if it grows
# large enough that a daily full tar becomes slow/wasteful) ---
AUDIO_ARCHIVE="$BACKUP_DIR/vaani_audio_cache_${TIMESTAMP}.tar.gz"
if tar -czf "$AUDIO_ARCHIVE" -C /var/www/vaani-backend audio_cache 2>>"$LOG_FILE"; then
    AUDIO_SIZE=$(du -h "$AUDIO_ARCHIVE" | cut -f1)
    log "Audio cache archive OK: $AUDIO_ARCHIVE ($AUDIO_SIZE)"
else
    log "WARNING: audio cache archive failed (non-fatal, DB backup already succeeded)"
    rm -f "$AUDIO_ARCHIVE"
fi

# --- Rotation: delete backups older than RETENTION_DAYS ---
DELETED=$(find "$BACKUP_DIR" -name "vaani_*.gz" -mtime "+$RETENTION_DAYS" -print -delete | wc -l)
log "Rotation: deleted $DELETED backup(s) older than $RETENTION_DAYS days"

log "Backup $TIMESTAMP complete"
