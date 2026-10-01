import base64
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from django.conf import settings
from django.utils import timezone


BACKUP_NAME_PATTERN = re.compile(
    r"^db-(?:scheduled|manual)-(\d{8}-\d{6}-\d{6})\.sqlite3$"
)


class BackupError(RuntimeError):
    pass


@dataclass
class BackupResult:
    path: Path
    uploaded_to_nextcloud: bool
    local_files_removed: int
    remote_files_removed: int


def perform_database_backup(kind="scheduled", now=None):
    if kind not in {"scheduled", "manual"}:
        raise ValueError("Unbekannte Backup-Art.")

    backup_time = _local_naive(now or timezone.now())
    backup_path = create_sqlite_backup(kind, backup_time)
    uploaded = False
    remote_removed = 0
    remote_error = None

    share_url = settings.NEXTCLOUD_BACKUP_SHARE_URL.strip()
    if share_url:
        try:
            client = NextcloudPublicShareClient(
                share_url,
                settings.NEXTCLOUD_BACKUP_SHARE_PASSWORD,
                timeout=settings.NEXTCLOUD_BACKUP_TIMEOUT,
            )
            client.upload(backup_path)
            uploaded = True
            remote_names = client.list_backup_names()
            remote_to_remove = backups_to_remove(
                remote_names,
                backup_time,
                settings.DB_BACKUP_KEEP_DAILY,
                settings.DB_BACKUP_KEEP_MONTHLY,
                settings.DB_BACKUP_KEEP_YEARLY,
            )
            for name in remote_to_remove:
                client.delete(name)
            remote_removed = len(remote_to_remove)
        except (
            BackupError,
            HTTPError,
            URLError,
            OSError,
            ValueError,
            ElementTree.ParseError,
        ) as error:
            remote_error = BackupError(
                f"Lokales Backup {backup_path.name} wurde erstellt, aber der "
                f"Nextcloud-Upload ist fehlgeschlagen: {error}"
            )

    local_names = [path.name for path in backup_path.parent.iterdir() if path.is_file()]
    local_to_remove = backups_to_remove(
        local_names,
        backup_time,
        settings.DB_BACKUP_KEEP_DAILY,
        settings.DB_BACKUP_KEEP_MONTHLY,
        settings.DB_BACKUP_KEEP_YEARLY,
    )
    for name in local_to_remove:
        (backup_path.parent / name).unlink()

    if remote_error is not None:
        raise remote_error

    return BackupResult(
        path=backup_path,
        uploaded_to_nextcloud=uploaded,
        local_files_removed=len(local_to_remove),
        remote_files_removed=remote_removed,
    )


def create_sqlite_backup(kind, backup_time):
    database = settings.DATABASES["default"]
    if database["ENGINE"] != "django.db.backends.sqlite3":
        raise BackupError("Die Backup-Funktion unterstützt derzeit nur SQLite.")

    source_path = Path(database["NAME"])
    if not source_path.is_file():
        raise BackupError(f"SQLite-Datenbank wurde nicht gefunden: {source_path}")

    backup_dir = Path(settings.DB_BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    filename = f"db-{kind}-{backup_time:%Y%m%d-%H%M%S-%f}.sqlite3"
    destination_path = backup_dir / filename
    temporary = tempfile.NamedTemporaryFile(
        prefix=".db-backup-",
        suffix=".sqlite3.tmp",
        dir=backup_dir,
        delete=False,
    )
    temporary_path = Path(temporary.name)
    temporary.close()

    try:
        source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(source_uri, uri=True) as source_connection:
            with sqlite3.connect(temporary_path) as destination_connection:
                source_connection.backup(destination_connection)
                result = destination_connection.execute("PRAGMA quick_check").fetchone()
                if result != ("ok",):
                    raise BackupError(
                        f"SQLite-Integritätsprüfung fehlgeschlagen: {result}"
                    )
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, destination_path)
    except BackupError:
        temporary_path.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.Error) as error:
        temporary_path.unlink(missing_ok=True)
        raise BackupError(f"SQLite-Backup ist fehlgeschlagen: {error}") from error

    return destination_path


def backups_to_remove(names, now, keep_daily, keep_monthly, keep_yearly):
    backups = []
    for name in names:
        match = BACKUP_NAME_PATTERN.fullmatch(name)
        if match is None:
            continue
        timestamp = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S-%f")
        backups.append((timestamp, name))

    backups.sort(reverse=True)
    keep = set()
    recent_cutoff = _local_naive(now) - timedelta(days=max(keep_daily, 0))
    if keep_daily > 0:
        keep.update(name for timestamp, name in backups if timestamp >= recent_cutoff)

    covered_months = {
        (timestamp.year, timestamp.month)
        for timestamp, name in backups
        if name in keep
    }
    monthly_added = 0
    for timestamp, name in backups:
        month = (timestamp.year, timestamp.month)
        if name in keep or month in covered_months:
            continue
        if monthly_added >= max(keep_monthly, 0):
            break
        keep.add(name)
        covered_months.add(month)
        monthly_added += 1

    covered_years = {
        timestamp.year for timestamp, name in backups if name in keep
    }
    yearly_added = 0
    for timestamp, name in backups:
        if name in keep or timestamp.year in covered_years:
            continue
        if yearly_added >= max(keep_yearly, 0):
            break
        keep.add(name)
        covered_years.add(timestamp.year)
        yearly_added += 1

    return sorted(name for _, name in backups if name not in keep)


class NextcloudPublicShareClient:
    def __init__(self, share_url, password, timeout=60):
        self.password = password
        self.timeout = timeout
        self._candidates = _nextcloud_webdav_candidates(share_url)
        self._active_candidate = None

    def list_backup_names(self):
        response = self._request("PROPFIND", headers={"Depth": "1"}, data=b"")
        root = ElementTree.fromstring(response)
        names = set()
        for href in root.findall(".//{DAV:}href"):
            if not href.text:
                continue
            path = urlsplit(unquote(href.text)).path.rstrip("/")
            name = path.rsplit("/", 1)[-1]
            if BACKUP_NAME_PATTERN.fullmatch(name):
                names.add(name)
        return names

    def upload(self, path):
        self._ensure_candidate()
        self._request("PUT", name=path.name, data=path.read_bytes())

    def delete(self, name):
        if BACKUP_NAME_PATTERN.fullmatch(name) is None:
            raise ValueError("Es dürfen nur eigene Backup-Dateien gelöscht werden.")
        self._request("DELETE", name=name)

    def _ensure_candidate(self):
        if self._active_candidate is None:
            self.list_backup_names()

    def _request(self, method, name=None, headers=None, data=None):
        candidates = (
            [self._active_candidate]
            if self._active_candidate is not None
            else self._candidates
        )
        last_error = None
        for candidate in candidates:
            base_url, username = candidate
            url = base_url if name is None else f"{base_url}/{quote(name)}"
            request_headers = {
                "X-Requested-With": "XMLHttpRequest",
                **(headers or {}),
            }
            if self.password or username:
                credentials = base64.b64encode(
                    f"{username}:{self.password}".encode("utf-8")
                ).decode("ascii")
                request_headers["Authorization"] = f"Basic {credentials}"
            request = Request(
                url,
                data=data,
                headers=request_headers,
                method=method,
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                self._active_candidate = candidate
                return body
            except HTTPError as error:
                last_error = error
                if self._active_candidate is not None or error.code not in {
                    401,
                    403,
                    404,
                    405,
                }:
                    raise
        if last_error is not None:
            raise last_error
        raise BackupError("Keine unterstützte Nextcloud-WebDAV-Adresse gefunden.")


def _nextcloud_webdav_candidates(share_url):
    parsed = urlsplit(share_url.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Der Nextcloud-Link muss eine vollständige HTTPS-URL sein.")

    marker = "/s/"
    marker_index = parsed.path.rfind(marker)
    if marker_index < 0:
        raise ValueError("Der Nextcloud-Link enthält keinen öffentlichen Share-Token.")
    token = parsed.path[marker_index + len(marker):].strip("/").split("/", 1)[0]
    if not token:
        raise ValueError("Der Nextcloud-Link enthält keinen Share-Token.")

    root_path = parsed.path[:marker_index].rstrip("/")
    origin = urlunsplit((parsed.scheme, parsed.netloc, root_path, "", ""))
    modern_url = f"{origin}/public.php/dav/files/{quote(token)}"
    legacy_url = f"{origin}/public.php/webdav"
    return [
        (modern_url, "anonymous"),
        (modern_url, token),
        (legacy_url, token),
    ]


def _local_naive(value):
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.replace(tzinfo=None)
