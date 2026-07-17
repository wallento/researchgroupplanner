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
		env_file:
			- .env          # Secrets nie in docker-compose.yml eintragen!
		volumes:
			- ./data:/data
		expose:
			- "8000"
```

Alle Konfigurationswerte kommen aus einer `.env` Datei auf dem Server
(niemals ins Git einchecken!):

```shell
# .env
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://example.com
DJANGO_DB_NAME=/data/db.sqlite3

# Email notifications (optional)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.com
EMAIL_PORT=25
EMAIL_USE_TLS=0
EMAIL_USE_SSL=0
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=geheimes-passwort
DEFAULT_FROM_EMAIL=noreply@example.com

# SAP WebGUI integration (optional)
SAP_ENABLED=0
SAP_URL=https://sap.example.com/sap/bc/gui/sap/its/webgui
SAP_USER=
SAP_PASSWORD=
SAP_FINANZSTELLE=
SAP_DATA_DIR=/data/sap
SAP_BROWSER=firefox
SAP_HEADLESS=1
SAP_SYNC_CRON=0 5 * * *
```

Die `.env` Datei zum `.gitignore` hinzufügen:

```shell
echo ".env" >> .gitignore
chmod 600 .env
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

## SAP WebGUI Integration

Die SAP-Anbindung ist standardmäßig deaktiviert. Für einen manuellen Test müssen
`SAP_ENABLED=1` sowie URL, Benutzer, Passwort und Finanzstelle gesetzt sein. Die
Zugangsdaten werden ausschließlich aus der Umgebung gelesen und weder in Django
noch in den Download-Metadaten gespeichert.

Der aktuell integrierte WebGUI-Adapter entspricht dem als Referenz verwendeten
Würzburger SAP-Ablauf. Andere Installationen können später über `SAP_BACKEND`
einen eigenen Adapter konfigurieren.

Ein manueller Abruf für das aktuelle Geschäftsjahr wird so gestartet:

```shell
python manage.py sync_sap
```

Ein anderes Jahr kann explizit angegeben werden:

```shell
python manage.py sync_sap --year 2025
```

Der Befehl lädt Budget, Ist und Obligo nach
`$SAP_DATA_DIR/raw/<Jahr>/` und aktualisiert anschließend atomar die Datei
`$SAP_DATA_DIR/last_download.json`. Im Docker-Image werden Firefox ESR und
Geckodriver mitgeliefert. Lokal kann alternativ Chrome mit `SAP_BROWSER=chrome`
verwendet werden; Selenium verwaltet den passenden Treiber dann beim ersten
Start.

Bereits heruntergeladene Exporte können ohne erneuten SAP-Zugriff verarbeitet
werden:

```shell
python manage.py parse_sap --year 2026
```

Dabei entsteht unter `$SAP_DATA_DIR/processed/<Jahr>.json` ein atomar
aktualisierter Web-Cache. Er enthält nur die aktiven, im Django-Admin
konfigurierten Fonds. Buchungen werden nicht in der Django-Datenbank gespeichert.

Die staff-geschützte Webansicht ist bei aktivierter Integration unter
`/ist-stand/` erreichbar. Sie bietet eine Jahresauswahl, eine Übersicht aller
aktiven Fonds sowie Kontoauszüge mit getrennten Spalten für bezahlte Buchungen
und grau markiertes Obligo. Fonds ohne Zeile im SAP-Budgetexport werden als
„kein SAP-Budget“ dargestellt; für sie wird noch kein Restbetrag berechnet.

Bei `SAP_ENABLED=1` wird außerdem ein täglicher SAP-Cronjob eingerichtet. Die
Standardzeit ist 05:00 Uhr und kann über `SAP_SYNC_CRON` geändert werden. Da der
Job `sync_sap` ohne `--year` aufruft, wird bei jeder Ausführung automatisch das
aktuelle Geschäftsjahr in der konfigurierten Django-Zeitzone verwendet. Das
Cron-Log liegt im Container unter `/tmp/cron_sap.log`. Nach Änderungen an
Feature-Flag oder Zeitplan muss der Container neu gestartet werden, damit
`django-crontab` den Eintrag neu anlegt.

## Email Notifications

**Email notifications are sent automatically every day at 8:00 AM** for:
- Staff members with upcoming contract expiries (30 days before)
- Project milestones due soon (30 days before)

Only staff members with `is_leadership=True` and a valid email address will receive notifications.

### Email Configuration

Set these environment variables in your `docker-compose.yml`:

- `EMAIL_BACKEND`: Usually `django.core.mail.backends.smtp.EmailBackend`
- `EMAIL_HOST`: Your SMTP server address
- `EMAIL_PORT`: Usually `25`, `587` (TLS), or `465` (SSL)
- `EMAIL_USE_TLS`: Set to `"1"` for port 587, `"0"` otherwise
- `EMAIL_USE_SSL`: Set to `"1"` for port 465, `"0"` otherwise
- `EMAIL_HOST_USER`: SMTP username (if required)
- `EMAIL_HOST_PASSWORD`: SMTP password (if required)
- `DEFAULT_FROM_EMAIL`: Sender email address

### SSL Certificate Verification Issues

If you get `SSL: CERTIFICATE_VERIFY_FAILED` errors (common with self-signed or internal certificates), use the insecure backend:

```yaml
EMAIL_BACKEND: "controlling.email_backend.InsecureEmailBackend"
```

⚠️ This disables SSL certificate verification. Only use this for internal/development SMTP servers with self-signed certificates.

**Note:** Email notifications for contract expiries and milestones are sent automatically every day at 8:00 AM (see environment variables above).

The image is published to GitHub Container Registry via the workflow in `.github/workflows/docker-publish.yml`.
