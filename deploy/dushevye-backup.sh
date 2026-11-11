#!/usr/bin/env bash
# Daily backup of Dushevye on the VDS. Keeps the last 7 archives.
# Copies configs. Does not modify sshd, nginx, or the live databases.
# Paths can be overridden only for a local test run.
set -euo pipefail
umask 077

STAMP=$(date +%Y-%m-%d_%H%M)
DEST_DIR=${DUSHEVYE_BACKUP_DIR:-/var/backups/dushevye}
SITE=${DUSHEVYE_SITE:-/var/www/dushevye}
OUT="${DEST_DIR}/dushevye-${STAMP}.tgz"

install -d -m 700 "$DEST_DIR"
chmod 700 "$DEST_DIR"

TMP=$(mktemp -d)
chmod 700 "$TMP"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/site" "$TMP/etc/nginx" "$TMP/etc/ssh" "$TMP/etc/systemd" "$TMP/etc/cron.d" "$TMP/etc/dushevye" "$TMP/cron"

# Site tree without live SQLite files. Those are snapshotted below so the
# archive does not contain a half-written app.db or catalog.db.
if [[ -d "$SITE" ]]; then
  mkdir -p "$TMP/site/dushevye"
  tar -C "$SITE" \
    --exclude='data/app.db' \
    --exclude='data/app.db-wal' \
    --exclude='data/app.db-shm' \
    --exclude='data/catalog.db' \
    --exclude='data/catalog.db-wal' \
    --exclude='data/catalog.db-shm' \
    -cf - . | tar -C "$TMP/site/dushevye" -xf -
fi

snapshot_sqlite() {
  local src="$1" dest="$2"
  if [[ ! -f "$src" ]]; then
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  python3 - "$src" "$dest" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
  chmod 600 "$dest"
}

snapshot_sqlite "$SITE/data/app.db" "$TMP/site/dushevye/data/app.db"
snapshot_sqlite "$SITE/data/catalog.db" "$TMP/site/dushevye/data/catalog.db"

copy_file() {
  local src="$1" dest_dir="$2"
  if [[ -f "$src" ]]; then
    cp -a "$src" "$dest_dir/"
  fi
}

shopt -s nullglob
copy_file /etc/nginx/sites-available/dushevye "$TMP/etc/nginx"
copy_file /etc/nginx/sites-enabled/dushevye "$TMP/etc/nginx"
for f in /etc/nginx/conf.d/*dushevye*; do
  copy_file "$f" "$TMP/etc/nginx"
done
# sshd configuration only. Host private keys are not part of this archive.
copy_file /etc/ssh/sshd_config "$TMP/etc/ssh"
for f in /etc/ssh/sshd_config.d/*; do
  if [[ -f "$f" ]]; then
    mkdir -p "$TMP/etc/ssh/sshd_config.d"
    cp -a "$f" "$TMP/etc/ssh/sshd_config.d/"
  fi
done
copy_file /etc/systemd/system/dushevye-api.service "$TMP/etc/systemd"
copy_file /etc/cron.d/dushevye "$TMP/etc/cron.d"
if [[ -d /etc/dushevye ]]; then
  cp -a /etc/dushevye/. "$TMP/etc/dushevye/"
fi
if crontab -l >"$TMP/cron/root.crontab" 2>/dev/null; then
  chmod 600 "$TMP/cron/root.crontab"
else
  rm -f "$TMP/cron/root.crontab"
fi

{
  echo "stamp=$STAMP"
  echo "host=$(hostname)"
  date -Is
  echo "note=sqlite backup API for app.db and catalog.db; ssh host keys are not included"
} > "$TMP/META.txt"

# Private files stay private inside the archive (umask 077).
find "$TMP" -type d -exec chmod 700 {} +
find "$TMP" -type f -exec chmod 600 {} +

tar -C "$TMP" -czf "$OUT" .
chmod 600 "$OUT"
# Tighten archives already sitting in the directory. Do not delete them here
# except for the usual 7-copy retention.
chmod 600 "$DEST_DIR"/dushevye-*.tgz 2>/dev/null || true
ls -1t "$DEST_DIR"/dushevye-*.tgz 2>/dev/null | tail -n +8 | xargs -r rm -f
echo "OK $OUT"
ls -lh "$OUT"
