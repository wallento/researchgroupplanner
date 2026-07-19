# Research Group Planner

## Local Setup

Set up the Python environment and install the dependencies:

```shell
pip install -r requirements.txt
```

Alternatively, use `uv` and `direnv` to create and activate a virtual environment:

```shell
uv venv
echo "source .venv/bin/activate" > .envrc
direnv allow
uv pip install -r requirements.txt
```

### Frontend Assets

The JavaScript and CSS dependencies are installed through npm and copied to
`controlling/static/vendor/`. No CDN is required at runtime.

```shell
npm install
npm run build:assets
```

Run these commands again whenever the frontend dependencies are updated.

Set up the Django database:

```shell
python manage.py makemigrations projects staffing
python manage.py migrate
```

Create an administrator account:

```shell
python manage.py createsuperuser
```

Start the development server:

```shell
python manage.py runserver
```

Open <http://localhost:8000/admin/> to create and manage application data.

## Docker

The Docker build creates the frontend assets automatically through a multi-stage
build with separate Node.js and Python stages. No manual CDN access is required
at runtime.

Example `docker-compose.yml` configuration behind Traefik:

```yaml
services:
  web:
    image: ghcr.io/myzinsky/researchgroupplanner:latest
    env_file:
      - .env # Never put secrets directly into docker-compose.yml.
    volumes:
      - ./data:/data
    expose:
      - "8000"
```

Store all configuration values in a `.env` file on the server. Never commit this
file to Git:

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
EMAIL_HOST_PASSWORD=secret-password
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

Protect the `.env` file locally as well:

```shell
chmod 600 .env
```

Generate a production secret key for `DJANGO_SECRET_KEY` with:

```shell
openssl rand -base64 48
```

After the first deployment, create a Django administrator once:

```shell
docker compose exec web python manage.py createsuperuser
```

The account is stored in the database and does not need to be recreated after a
restart.

The image is published to GitHub Container Registry through
`.github/workflows/docker-publish.yml`.

## Authentication

The complete web application requires authentication. Anonymous visitors are
redirected to the custom `/login/` page, which uses the Group Planning design.
After a successful login, users return to the page they originally requested.
They can sign out through the main navigation.

Manage user accounts in the Django admin under **Users**. Regular active users
can access the planner. The admin link and SAP account statements remain limited
to users with staff status.

## SAP WebGUI Integration

The SAP integration is disabled by default. For a manual test, set
`SAP_ENABLED=1` together with the SAP URL, username, password, and financial
center. Credentials are read exclusively from environment variables and are
never stored in Django or in the download metadata.

The included WebGUI adapter implements the Würzburg workflow used as the
reference integration. Other institutions can provide a different adapter
through `SAP_BACKEND`.

Run a manual synchronization for the current fiscal year:

```shell
python manage.py sync_sap
```

Specify a different year explicitly if required:

```shell
python manage.py sync_sap --year 2025
```

The command downloads budget, actual, and commitment reports to
`$SAP_DATA_DIR/raw/<year>/` and then atomically updates
`$SAP_DATA_DIR/last_download.json`. The Docker image includes Firefox ESR and
Geckodriver. For local development, Chrome can be selected with
`SAP_BROWSER=chrome`; Selenium will manage the appropriate driver on first use.

Process existing downloads without accessing SAP again:

```shell
python manage.py parse_sap --year 2026
```

This command creates an atomically updated web cache at
`$SAP_DATA_DIR/processed/<year>.json`. The cache contains only active funds that
are configured in the Django admin. Transactions are not stored in the Django
database.

When the integration is enabled, the staff-only web interface is available at
`/ist-stand/`. It provides a year selector, an overview of all active funds, and
account statements with separate columns for paid transactions and grey-highlighted
commitments. Funds without a row in the SAP budget export are marked as having no
SAP budget; no remaining amount is calculated for them yet.

Setting `SAP_ENABLED=1` also installs a daily synchronization job. It runs at
05:00 by default and can be changed through `SAP_SYNC_CRON`. The job invokes
`sync_sap` without `--year`, so every run automatically uses the current fiscal
year in the configured Django time zone. The container writes the cron output to
`/tmp/cron_sap.log`. Restart the container after changing the feature flag or
schedule so that `django-crontab` can update the entry.

## Email Notifications

Email notifications are sent automatically every day at 08:00 for:

- staff members whose contracts expire in 30 days;
- project milestones due in 30 days.

Only staff members with `is_leadership=True` and a valid email address receive
notifications.

### Email Configuration

Configure email delivery with these environment variables:

- `EMAIL_BACKEND`: usually `django.core.mail.backends.smtp.EmailBackend`;
- `EMAIL_HOST`: SMTP server address;
- `EMAIL_PORT`: usually `25`, `587` for TLS, or `465` for SSL;
- `EMAIL_USE_TLS`: set to `1` for port 587 and `0` otherwise;
- `EMAIL_USE_SSL`: set to `1` for port 465 and `0` otherwise;
- `EMAIL_HOST_USER`: SMTP username, if required;
- `EMAIL_HOST_PASSWORD`: SMTP password, if required;
- `DEFAULT_FROM_EMAIL`: sender email address.

### SSL Certificate Verification Issues

If an internal or self-signed certificate causes an
`SSL: CERTIFICATE_VERIFY_FAILED` error, the insecure email backend can be used:

```yaml
EMAIL_BACKEND: "controlling.email_backend.InsecureEmailBackend"
```

This disables TLS certificate verification. Use it only for trusted internal or
development SMTP servers with self-signed certificates.
