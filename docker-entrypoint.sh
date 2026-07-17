#!/bin/sh
set -eu

mkdir -p "$(dirname "${DJANGO_DB_NAME:-/app/db.sqlite3}")"

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Setup cron for daily notifications at 8:00 AM
apt-get update && apt-get install -y --no-install-recommends cron procps
mkdir -p /var/log
cat > /tmp/crontab.txt << 'EOF'
0 8 * * * cd /app && /usr/local/bin/python manage.py send_notifications >> /var/log/send_notifications.log 2>&1
* * * * * cd /app && /usr/local/bin/python manage.py send_test_email >> /var/log/send_test_email.log 2>&1
EOF
crontab /tmp/crontab.txt
# Start cron in background
cron -f &

# Start gunicorn in foreground
gunicorn groupplanning.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${GUNICORN_WORKERS:-3} --timeout ${GUNICORN_TIMEOUT:-60}