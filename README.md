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
      - ./branding:/app/branding:ro # Optional deployment-specific branding.
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

# Deployment-specific controlling features
OVERHEAD_SPLIT_ENABLED=1
LANDESSTELLEN_ENABLED=1
ANNUAL_POOLS_ENABLED=1
BRAND_LOGO=controlling/img/Symbol_White.svg

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
# SAP Grants Management project export upload (optional)
SAP_GM_IMPORT_ENABLED=0

# SQLite backups (optional)
DB_BACKUP_ENABLED=0
DB_BACKUP_DIR=/data/backups
DB_BACKUP_CRON=0 2 * * *
DB_BACKUP_KEEP_DAILY=7
DB_BACKUP_KEEP_MONTHLY=12
DB_BACKUP_KEEP_YEARLY=1

# Password-protected public Nextcloud folder share (optional)
NEXTCLOUD_BACKUP_SHARE_URL=https://cloud.example.com/s/SHARE_TOKEN
NEXTCLOUD_BACKUP_SHARE_PASSWORD=strong-share-password
NEXTCLOUD_BACKUP_TIMEOUT=60
```

### Persistent Docker Data

The `./data:/data` bind mount keeps both the SQLite database and SAP files on
the Docker host. With `SAP_DATA_DIR=/data/sap`, raw exports, processed JSON
caches, and download metadata are stored below `./data/sap/` and therefore
survive container restarts, image updates, and container recreation. Do not
point `SAP_DATA_DIR` at a path below `/app` in production unless that path has
its own persistent mount.

If an existing container still contains SAP files at the old default path,
copy them to the host before recreating it:

```shell
mkdir -p data/sap
docker compose cp web:/app/sap_data/. ./data/sap/
```

Protect the `.env` file locally as well:

```shell
chmod 600 .env
```

### SQLite Backups and Nextcloud

When `DB_BACKUP_ENABLED=1`, the application creates a verified SQLite backup at
the configured cron time. Backups are regular `.sqlite3` files and are written
to `DB_BACKUP_DIR` through SQLite's online backup API, so the running web
container does not need to be stopped. The backup is only published under its
final name after `PRAGMA quick_check` succeeds.

The default retention policy keeps:

- every backup created within the last 7 days, including manual safety backups;
- one additional backup for each of the previous 12 months;
- one additional yearly backup.

Only files created by this application and matching its strict backup filename
format are considered for deletion. Other files in the local or remote folder
are left untouched.

To store the same plain SQLite files in Nextcloud, create a dedicated folder,
share it through a password-protected public link, and select **Allow upload and
editing**. Do not use **File drop**, because rotation requires permission to
list and delete old backups. Put the public share URL and password in `.env` as
shown above. The application supports the current and legacy public-share
WebDAV endpoints automatically.

Staff users can create an additional safety backup from the Statistics page.
The same operation is available on the command line:

```shell
docker compose exec web python manage.py backup_database
```

Restart the container after enabling backups or changing `DB_BACKUP_CRON` so
that the container's crontab is regenerated.

To restore a backup, stop the web container, preserve the current database, and
replace it with the selected plain SQLite file:

```shell
docker compose stop web
cp data/db.sqlite3 data/db-before-restore.sqlite3
cp data/backups/db-manual-YYYYMMDD-HHMMSS-ffffff.sqlite3 data/db.sqlite3
docker compose up -d web
```

The container entrypoint applies any migrations that are newer than the restored
database when the service starts again.

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

After pulling a version that contains new database migrations, apply them with:

```shell
docker compose exec web python manage.py migrate
```

The provided Docker entrypoint performs this step automatically when the
container starts.

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

## Deployment-Specific Features

Several controlling features can be adapted to the organization through
environment variables. All of them are enabled by default.

### Overhead Distribution

Set `OVERHEAD_SPLIT_ENABLED=0` for deployments that do not split overhead funds
between the local chair and other institutes. In this mode:

- the complete overhead amount is treated as available;
- institute-share details are hidden from the dashboard;
- overhead-distribution statistics are hidden;
- warnings about incomplete overhead distributions are disabled.

Individual projects that are not eligible for overhead can be marked with the
**No overhead** checkbox in the Django admin. These projects are identified on
their detail page and do not trigger the missing-overhead warning.

### Optional Funding Sources

Set `LANDESSTELLEN_ENABLED=0` or `ANNUAL_POOLS_ENABLED=0` when the corresponding
funding-source concept is not used by a deployment. The related navigation,
dashboard sections, and allocation warnings are then hidden. Existing database
records are retained, so the features can be enabled again later.

### Custom Navbar Logo

`BRAND_LOGO` controls the logo displayed in the main navigation. The built-in
default is:

```shell
BRAND_LOGO=controlling/img/Symbol_White.svg
```

To use a deployment-specific logo, place it in the Git-ignored `branding/`
directory and set `BRAND_LOGO` to its path relative to that directory:

```shell
mkdir -p branding
cp /path/to/your/logo.svg branding/logo.svg
export BRAND_LOGO=logo.svg
```

For Docker, mount the directory at `/app/branding` as shown in the Compose
example above. The container collects the asset during startup. Set
`BRAND_LOGO=` to hide the navbar logo entirely.

## Staffing and Validation Workflows

The main dashboard displays current staff only; people whose status is
`alumni` remain available in the database and on their detail pages but are
excluded from the current-staff timeline.

When two salary records overlap, the warnings page now shows the affected dates
and salary amounts and provides a **Merge** action. After confirmation, the two
records are atomically replaced by non-overlapping periods. Salaries are added
together for the overlap while the original salary is preserved before and
after it.

Salary records are monthly amounts by default. If **Exact amount?** is enabled,
the entered value is treated as the complete amount for the selected date range
and is not prorated a second time. Exact amounts must stay within one calendar
month. During migration, existing records that cover only part of one calendar
month are marked as exact; full-month and multi-month records remain monthly.

Project lists link directly to each project's staffing timeline. Timeline labels
use a more compact layout, and currency values use non-breaking spacing so that
the euro sign stays attached to its amount.

Personnel funding allocations support project budget items, state-funded
positions, annual pool budgets, and a source-independent **Universal project**.
Exactly one funding source must be selected for each allocation. Universal
allocations appear in staff details and the main timeline but are not charged to
a project's planning budget.

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
If Selenium warns about a Firefox/Geckodriver version mismatch in Docker,
rebuild and redeploy the image so the bundled driver version is updated.

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

Account statements also offer a cleaned view. It removes exact counter-bookings
and groups transactions with the same type, business partner, and position. For
funds using payment requests, enable **Treat negative actual postings as funding**
in the SAP fund configuration. After counter-bookings have been removed, remaining
negative actual postings are then shown in green as funding receipts and excluded
from paid, actual-plus-commitments, and remaining-budget calculations. The fund
overview uses the same adjusted values and marks them accordingly, while the
original account statement always preserves the unmodified SAP figures.

For project-owned funds, the overview also compares the sum of adjusted actuals
and commitments across every available fiscal year and every active fund of the
project with the total grant budget stored in Planning. The resulting lifetime
utilization percentage and total project budget are shown independently of the
selected annual view. Annual pools, universal funds, and other funds without a
project assignment do not receive a project-level budget or utilization value.
The overview also shows the elapsed share of each project's lifetime, based on
its start date and effective end date. A cost-neutral extension is used as the
effective end date when configured; progress is bounded between zero and one
hundred percent.

### Reconciling Planned and Actual Salaries

The warnings page compares monthly planned salaries with cleaned, paid SAP
transactions whose position follows `Gehalt <month> <year>`. Salary components
for the same person and month are summed across all active SAP funds. Staff are
matched automatically using normalized first and last names. If the SAP name
differs, set the optional **SAP business partner** field on the staff member.
If a person changes employment within a month, the source SAP fund is matched
against existing project, annual-pool, or universal funding allocations to
select the employment. The update remains blocked when this does not produce
exactly one match.

Differences are never applied automatically. For an unambiguous period, the
warning offers **Apply actual to planning**. After confirmation, only that SAP
period is replaced by the SAP amount; the existing salary period is split so
earlier and future planned values remain unchanged. SAP commitments with a
partial-month date range and actual salaries in a partial employment month are
stored as exact amounts. Overlapping salary records, missing employments, source
conflicts, and ambiguous person matches are reported without an apply button and
must be resolved manually first.

Setting `SAP_ENABLED=1` also installs a daily synchronization job. It runs at
05:00 by default and can be changed through `SAP_SYNC_CRON`. The job invokes
`sync_sap` without `--year`, so every run automatically uses the current fiscal
year in the configured Django time zone. The container writes the cron output to
`/tmp/cron_sap.log`. Restart the container after changing the feature flag or
schedule so that `django-crontab` can update the entry.

### Project exports from SAP Grants Management (GM_E_4GBA)

Institutions using SAP Grants Management can upload the line-item export
(report GM_E_4GBA, "Budget und Obligo/Ist") per project instead of using the
WebGUI download. Enable it with `SAP_GM_IMPORT_ENABLED=1`; no SAP credentials
are used. It is independent of `SAP_ENABLED`, which controls the WebGUI
download, the fiscal-year pages and the daily sync job.

1. Enter the project's PSP-Element (e.g. `1/070004900`) as fund number of a
   SAP fund on the project in the Django admin.
2. Upload the `.xlsx` export on `/ist-stand/`, or import it on the command line:

   ```shell
   python manage.py import_sap_export EXPORT.xlsx
   ```

   The project is detected from the PSP-Element. A new import replaces the
   previous one for that project. Dates of birth in payroll texts are dropped.

The import groups all bookings into positions: each staff contract
(Mittelreservierung, with person, contract periods and payroll per month),
each trip, each purchase (requisition → order → invoice) and each funder
payment. The reconciliation page (`/ist-stand/abgleich/<fund>/`) cross-checks
them with the planning:

- Staff positions are matched to funding allocations through the allocation's
  SAP reference or the person's name. Differences in contract period, extent,
  monthly salary and open commitments are reported.
- Travel and other positions are matched to budget transactions through
  `sap_id`. Each E/A-Art is mapped onto a planning budget item on the page.
- Positions can be transferred into the planning (employment, allocation and
  salaries, or a budget transaction), linked to existing entries, or ignored.
  Ignored positions and name mappings persist across imports.

Open differences are also listed on the warnings page.

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
