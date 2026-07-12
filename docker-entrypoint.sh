#!/bin/sh
set -eu

mkdir -p "$(dirname "${DJANGO_DB_NAME:-/app/db.sqlite3}")"

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Setup cron for daily notifications at 8:00 AM
apt-get update && apt-get install -y --no-install-recommends cron
echo "0 8 * * * cd /app && python manage.py send_notifications >> /var/log/send_notifications.log 2>&1" | crontab -
mkdir -p /var/log
cron -f &

exec gunicorn groupplanning.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${GUNICORN_WORKERS:-3} --timeout ${GUNICORN_TIMEOUT:-60}