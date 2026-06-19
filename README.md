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
		volumes:
			- ./data:/data
		expose:
			- "8000"

```

The image is published to GitHub Container Registry via the workflow in `.github/workflows/docker-publish.yml`.