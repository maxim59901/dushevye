#!/usr/bin/env bash
# Run ON the VDS as root. Expects this directory extracted, e.g. /tmp/dushevye-payload/
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
SITE=/var/www/dushevye
PUB="$SITE/public"
APP="$SITE/app"
DATA="$SITE/data"

mkdir -p "$PUB" "$DATA" /etc/dushevye
install -d -m 700 /var/backups/dushevye
chmod 700 /var/backups/dushevye
install -d -m 700 /var/backups/dushevye/pre-deploy
chmod 700 /var/backups/dushevye/pre-deploy

# --- static site ---
# Previous installs left index.html.bak.* inside the web root (they answered 200).
# Keep the previous page, but outside public/, and move any already public copies.
shopt -s nullglob
for old in "$PUB"/index.html.bak "$PUB"/index.html.bak.*; do
  if [[ -f "$old" ]]; then
    mv "$old" /var/backups/dushevye/pre-deploy/
  fi
done
shopt -u nullglob
if [[ -f "$PUB/index.html" ]]; then
  prev="/var/backups/dushevye/pre-deploy/index.html.$(date +%Y%m%d_%H%M%S)"
  cp -a "$PUB/index.html" "$prev"
  chmod 600 "$prev"
fi
cp -a "$BASE/public/index.html" "$PUB/index.html"
cp -a "$BASE/public/cfg.html" "$PUB/cfg.html"
# other root public assets (assistant widget etc.)
for f in "$BASE"/public/*; do
  base=$(basename "$f")
  case "$base" in
    index.html|cfg.html|lk|media|README.md) continue ;;
  esac
  if [[ -f "$f" ]]; then
    cp -a "$f" "$PUB/$base"
  fi
done
rm -rf "$PUB/lk"
cp -a "$BASE/public/lk" "$PUB/lk"
# media (product photos)
mkdir -p "$PUB/media"
if [[ -d "$BASE/public/media" ]]; then
  cp -a "$BASE/public/media/." "$PUB/media/"
fi
# catalog.db is the live supplier database. A payload must not contain it and
# this install never copies one over the file on disk.
mkdir -p "$DATA"
if [[ -f "$BASE/data/catalog/catalog.db" ]]; then
  echo "catalog.db: payload file ignored; live $DATA/catalog.db is not overwritten"
fi
if [[ ! -f "$DATA/catalog.db" ]]; then
  echo "catalog.db: none on disk (payload cannot create it)"
fi
chown -R www-data:www-data "$PUB/media" 2>/dev/null || true
cp -a "$BASE/README.md" "$SITE/README.md" 2>/dev/null || true
if [[ -f "$BASE/DEPLOYED_SHA.txt" ]]; then
  cp -a "$BASE/DEPLOYED_SHA.txt" "$SITE/DEPLOYED_SHA.txt"
fi

# --- backup cron ---
# One schedule only. Older installs wrote both root crontab and /etc/cron.d,
# so the job ran twice. Keep unrelated crontab lines and unrelated lines in
# /etc/cron.d/dushevye. Do not edit sshd here.
if [[ -f "$BASE/dushevye-backup.sh" ]]; then
  install -m 0700 "$BASE/dushevye-backup.sh" /usr/local/bin/dushevye-backup.sh
  CRON_FILE=/etc/cron.d/dushevye
  CRON_LINE='0 3 * * * root /usr/local/bin/dushevye-backup.sh >> /var/log/dushevye-backup.log 2>&1'
  touch "$CRON_FILE"
  cron_tmp=$(mktemp)
  if ! grep -q '^SHELL=' "$CRON_FILE"; then
    printf '%s\n' 'SHELL=/bin/bash' 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' > "$cron_tmp"
  fi
  grep -v 'dushevye-backup.sh' "$CRON_FILE" >> "$cron_tmp" || true
  printf '%s\n' "$CRON_LINE" >> "$cron_tmp"
  mv "$cron_tmp" "$CRON_FILE"
  chmod 644 "$CRON_FILE"
  if crontab -l >"${cron_tmp}.prev" 2>/dev/null; then
    if grep -q 'dushevye-backup.sh' "${cron_tmp}.prev"; then
      grep -v 'dushevye-backup.sh' "${cron_tmp}.prev" > "${cron_tmp}.next" || true
      if [[ -s "${cron_tmp}.next" ]]; then
        crontab "${cron_tmp}.next"
      else
        crontab -r || true
      fi
    fi
  fi
  rm -f "${cron_tmp}.prev" "${cron_tmp}.next"
fi

# --- python app ---
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip

rm -rf "$APP"
cp -a "$BASE/app" "$APP"
chown -R root:www-data "$APP"
# data dir writable by www-data
chown -R www-data:www-data "$DATA"
chmod 770 "$DATA"

if [[ ! -d "$SITE/venv" ]]; then
  python3 -m venv "$SITE/venv"
fi
"$SITE/venv/bin/pip" install -q --upgrade pip
"$SITE/venv/bin/pip" install -q -r "$APP/requirements.txt"

# session secret
if [[ ! -f /etc/dushevye/api.env ]]; then
  SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  umask 077
  cat > /etc/dushevye/api.env <<ENV
DUSHEVYE_SESSION_SECRET=$SECRET
DUSHEVYE_DB=$DATA/app.db
DUSHEVYE_CATALOG_DB=$DATA/catalog.db
# Set to 1 after HTTPS is enabled (Secure session cookie). Leave 0 on HTTP-by-IP.
DUSHEVYE_COOKIE_SECURE=0
ENV
  chmod 600 /etc/dushevye/api.env
fi
# ensure catalog db path in env
if ! grep -q DUSHEVYE_CATALOG_DB /etc/dushevye/api.env 2>/dev/null; then
  echo "DUSHEVYE_CATALOG_DB=$DATA/catalog.db" >> /etc/dushevye/api.env
fi

# seed admin (Maxim) if missing — password to /root/dushevye-initial-admin.txt
export DUSHEVYE_DB="$DATA/app.db"
export DUSHEVYE_ADMIN_PASS_FILE=/root/dushevye-initial-admin.txt
cd "$SITE"
# PYTHONPATH so `app` package resolves
PYTHONPATH="$SITE" "$SITE/venv/bin/python" -m app.seed || true
# ensure www-data can read/write db
chown www-data:www-data "$DATA/app.db" 2>/dev/null || true
chmod 660 "$DATA/app.db" 2>/dev/null || true

# systemd
install -m 0644 "$BASE/dushevye-api.service" /etc/systemd/system/dushevye-api.service
systemctl daemon-reload
systemctl enable dushevye-api
systemctl restart dushevye-api

# nginx. The repo file already denies dot paths and bak/sql/sqlite/db/env,
# and serves /media/ from root (no alias). sshd is not modified by this script.
install -m 0644 "$BASE/nginx-dushevye.conf" /etc/nginx/sites-available/dushevye
ln -sfn /etc/nginx/sites-available/dushevye /etc/nginx/sites-enabled/dushevye
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

sleep 1
systemctl --no-pager --full status dushevye-api | head -20 || true
curl -sS http://127.0.0.1:8088/api/health || true
echo
echo "INSTALL_VDS_OK"
ls -la "$PUB/lk" | head
ls -la "$DATA" || true
test -f /root/dushevye-initial-admin.txt && echo "ADMIN_PASS_FILE=/root/dushevye-initial-admin.txt" || echo "ADMIN_PASS_FILE=missing"
