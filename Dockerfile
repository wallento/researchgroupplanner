FROM node:20-alpine AS frontend-assets

WORKDIR /assets

COPY package.json package-lock.json /assets/
COPY tools /assets/tools

RUN npm ci && npm run build:assets


FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=0 \
    DJANGO_DB_NAME=/app/db.sqlite3

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    cron \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/
COPY --from=frontend-assets /assets/controlling/static/vendor /app/controlling/static/vendor

EXPOSE 8000

CMD ["sh", "/app/docker-entrypoint.sh"]