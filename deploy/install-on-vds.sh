#!/usr/bin/env bash
# Run ON the VDS as root. Expects this directory extracted, e.g. /tmp/dushevye-payload/
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
SITE=/var/www/dushevye
PUB="$SITE/public"
APP="$SITE/app"
DATA="$SITE/data"

mkdir -p "$PUB" "$DATA" /etc/dushevye /var/backups/dushevye

# --- static site ---
if [[ -f "$PUB/index.html" ]]; then
  cp -a "$PUB/index.html" "$PUB/index.html.bak.$(date +%Y%m%d_%H%M%S)"
fi
cp -a "$BASE/public/index.html" "$PUB/index.html"
cp -a "$BASE/public/cfg.html" "$PUB/cfg.html"
rm -rf "$PUB/lk"
cp -a "$BASE/public/lk" "$PUB/lk"
# media (product photos)
mkdir -p "$PUB/media"
if [[ -d "$BASE/public/media" ]]; then
  cp -a "$BASE/public/media/." "$PUB/media/"
fi
# catalog seed db/json — never overwrite a larger/newer remote DB with a smaller payload
mkdir -p "$DATA"
if [[ -f "$BASE/data/catalog/catalog.db" ]]; then
  LOCAL_SZ=$(stat -c%s "$BASE/data/catalog/catalog.db" 2>/dev/null || echo 0)
  REMOTE_SZ=0
  if [[ -f "$DATA/catalog.db" ]]; then
    REMOTE_SZ=$(stat -c%s "$DATA/catalog.db" 2>/dev/null || echo 0)
  fi
  if [[ "$LOCAL_SZ" -gt "$REMOTE_SZ" ]]; then
    echo "catalog.db: installing payload ($LOCAL_SZ > remote $REMOTE_SZ)"
    cp -a "$BASE/data/catalog/catalog.db" "$DATA/catalog.db"
    cp -a "$BASE/data/catalog/parts.json" "$DATA/parts.json" 2>/dev/null || true
    cp -a "$BASE/data/catalog/import-report.json" "$DATA/import-report.json" 2>/dev/null || true
  else
    echo "catalog.db: keeping remote ($REMOTE_SZ >= payload $LOCAL_SZ)"
  fi
elif [[ ! -f "$DATA/catalog.db" ]]; then
  echo "catalog.db: none in payload and none on disk"
fi
chown -R www-data:www-data "$PUB/media" 2>/dev/null || true
cp -a "$BASE/README.md" "$SITE/README.md" 2>/dev/null || true

# --- backup cron ---
if [[ -f "$BASE/dushevye-backup.sh" ]]; then
  install -m 0755 "$BASE/dushevye-backup.sh" /usr/local/bin/dushevye-backup.sh
  CRON_LINE='0 3 * * * /usr/local/bin/dushevye-backup.sh >> /var/log/dushevye-backup.log 2>&1'
  (crontab -l 2>/dev/null | grep -v 'dushevye-backup.sh' || true; echo "$CRON_LINE") | crontab -
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

# nginx
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
