#!/bin/sh
set -eu

mkdir -p "$(dirname "${DJANGO_DB_NAME:-/app/db.sqlite3}")"

python manage.py makemigrations --noinput
python manage.py migrate --noinput
python manage.py collectstatic --noinput

printenv > /etc/environment
chmod 600 /etc/environment

# Setup django-crontab
apt-get update && apt-get install -y --no-install-recommends cron
python manage.py crontab add

# Start supervisor to manage cron and gunicorn
exec supervisord -c /app/supervisord.conf