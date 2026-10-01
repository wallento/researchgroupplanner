FROM node:20-alpine AS frontend-assets

WORKDIR /assets

COPY package.json package-lock.json /assets/
COPY tools /assets/tools

RUN npm ci && npm run build:assets


FROM python:3.13-slim

ARG GECKODRIVER_VERSION=0.37.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=0 \
    DJANGO_DB_NAME=/app/db.sqlite3 \
    SAP_BROWSER=firefox \
    SAP_BROWSER_BINARY=/usr/bin/firefox-esr

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    cron \
    ca-certificates \
    curl \
    firefox-esr \
    tar \
    && architecture="$(dpkg --print-architecture)" \
    && case "$architecture" in \
        amd64) geckodriver_arch="linux64" ;; \
        arm64) geckodriver_arch="linux-aarch64" ;; \
        *) echo "Unsupported architecture for geckodriver: $architecture" >&2; exit 1 ;; \
    esac \
    && curl --fail --location --silent --show-error \
        "https://github.com/mozilla/geckodriver/releases/download/v${GECKODRIVER_VERSION}/geckodriver-v${GECKODRIVER_VERSION}-${geckodriver_arch}.tar.gz" \
        --output /tmp/geckodriver.tar.gz \
    && tar --extract --gzip --file /tmp/geckodriver.tar.gz --directory /usr/local/bin \
    && chmod 0755 /usr/local/bin/geckodriver \
    && rm -f /tmp/geckodriver.tar.gz \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/
COPY --from=frontend-assets /assets/controlling/static/vendor /app/controlling/static/vendor

EXPOSE 8000

CMD ["sh", "/app/docker-entrypoint.sh"]
