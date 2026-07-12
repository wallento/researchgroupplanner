# Build

First we have to setup our python envitonment:

```shell
pip install -r requirements.txt
```

An alternative would be using `uv` and `direnv` for a virtual environment:
``` shell
uv venv
echo "source .venv/bin/activate" > .envrc
direnv allow
uv pip install -r requirements.txt
```

## Frontend assets (lokal, ohne CDN)

Die JavaScript/CSS-Abhaengigkeiten werden via npm installiert und in
`controlling/static/vendor/` bereitgestellt.

```shell
npm install
npm run build:assets
```

Dieser Schritt sollte nach einem Update der Frontend-Abhaengigkeiten erneut
ausgefuehrt werden.

Then we setup Django

``` shell
python manage.py makemigrations projects staffing
python manage.py migrate
```

# Create admin

```shell
python manage.py createsuperuser
```

# Run

```shell
python manage.py runserver
```

# Create data in DB

Go to http://localhost:8000/admin

# Docker

Der Docker-Build erzeugt die Frontend-Assets automatisch ueber einen
Multi-Stage-Build (Node-Stage + Python-Stage). Es ist kein manueller
CDN-Zugriff zur Laufzeit notwendig.

Example for a `docker-compose.yml` behind Traefik:

```yaml
services:
	web:
		image: ghcr.io/myzinsky/researchgroupplanner:latest
		environment:
			DJANGO_SECRET_KEY: "change-me"
			DJANGO_DEBUG: "0"
			DJANGO_ALLOWED_HOSTS: "example.com"
			DJANGO_CSRF_TRUSTED_ORIGINS: "https://example.com"
			DJANGO_DB_NAME: /data/db.sqlite3
			# Email notifications (optional)
			EMAIL_BACKEND: "django.core.mail.backends.smtp.EmailBackend"
			EMAIL_HOST: "smtp.example.com"
			EMAIL_PORT: "25"
			EMAIL_USE_TLS: "0"
			EMAIL_USE_SSL: "0"
			EMAIL_HOST_USER: ""
			EMAIL_HOST_PASSWORD: ""
			DEFAULT_FROM_EMAIL: "noreply@example.com"
		volumes:
			- ./data:/data
		expose:
			- "8000"

```

You can generate a production secret key for `DJANGO_SECRET_KEY` like this:

```shell
openssl rand -base64 48
```

After the first deployment, you still need to create a Django admin user once:

```shell
docker compose exec web python manage.py createsuperuser
```

The user is stored in the database and does not need to be recreated on every restart.

**Note:** Email notifications for contract expiries and milestones are sent automatically every day at 8:00 AM (see environment variables above).

The image is published to GitHub Container Registry via the workflow in `.github/workflows/docker-publish.yml`.