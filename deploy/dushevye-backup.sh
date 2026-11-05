#!/usr/bin/env bash
# Daily backup of Dushevye site on VDS. Keep last 7 archives.
set -euo pipefail
STAMP=$(date +%Y-%m-%d_%H%M)
DEST_DIR=/var/backups/dushevye
SITE=/var/www/dushevye
OUT="${DEST_DIR}/dushevye-${STAMP}.tgz"
mkdir -p "$DEST_DIR"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/site" "$TMP/nginx"
if [[ -d "$SITE" ]]; then
  cp -a "$SITE" "$TMP/site/"
fi
# copy matching nginx site configs if present
shopt -s nullglob
for f in /etc/nginx/sites-enabled/*dushevye* /etc/nginx/sites-available/*dushevye* \
         /etc/nginx/sites-enabled/default /etc/nginx/conf.d/*dushevye*; do
  [[ -e "$f" ]] && cp -a "$f" "$TMP/nginx/" || true
done
# always include a short inventory
{
  echo "stamp=$STAMP"
  echo "host=$(hostname)"
  date -Is
  timedatectl 2>/dev/null | head -5 || true
  nginx -v 2>&1 || true
} > "$TMP/META.txt"

tar -C "$TMP" -czf "$OUT" .
chmod 640 "$OUT" || true
# retain last 7
ls -1t "$DEST_DIR"/dushevye-*.tgz 2>/dev/null | tail -n +8 | xargs -r rm -f
echo "OK $OUT"
ls -lh "$OUT"
