#!/bin/sh
set -eu

mkdir -p "$(dirname "${DJANGO_DB_NAME:-/app/db.sqlite3}")"

python manage.py makemigrations --noinput
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Setup django-crontab
apt-get update && apt-get install -y --no-install-recommends cron
python manage.py crontab add

# Start gunicorn in foreground
exec gunicorn groupplanning.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${GUNICORN_WORKERS:-3} --timeout ${GUNICORN_TIMEOUT:-60}