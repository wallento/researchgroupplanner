import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from django.utils.module_loading import import_string

from sap_integration.workbooks import same_nonempty_workbook_content


@dataclass(frozen=True)
class SAPSyncResult:
    year: int
    report_paths: dict[str, Path]
    completed_at: str


def run_sync(config, year):
    config.data_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sap-download-", dir=config.data_dir) as temp_dir:
        backend_class = import_string(config.backend)
        reports = backend_class(config).download(year, Path(temp_dir))
        _validate_reports(reports)

        target_dir = config.data_dir / "raw" / str(year)
        target_dir.mkdir(parents=True, exist_ok=True)
        published_reports = {}
        for report_name, source_path in reports.items():
            destination = target_dir / f"{report_name}.xlsx"
            os.replace(source_path, destination)
            published_reports[report_name] = destination

    completed_at = datetime.now(timezone.utc).isoformat()
    _write_status(config.data_dir, year, published_reports, completed_at)
    return SAPSyncResult(year, published_reports, completed_at)


def _validate_reports(reports):
    expected = {"budget", "actual", "commitments"}
    if set(reports) != expected:
        raise ValueError(
            "Das SAP-Backend muss Budget-, Ist- und Obligo-Export bereitstellen."
        )
    for report_path in reports.values():
        report_path = Path(report_path)
        if not report_path.is_file() or report_path.stat().st_size == 0:
            raise ValueError(f"Ungültiger SAP-Export: {report_path.name}")
    if same_nonempty_workbook_content(reports["actual"], reports["commitments"]):
        raise ValueError(
            "SAP hat für Ist und Obligo denselben nicht-leeren Export geliefert."
        )


def _write_status(data_dir, year, reports, completed_at):
    status = {
        "year": year,
        "completed_at": completed_at,
        "reports": {
            report_name: str(path.relative_to(data_dir))
            for report_name, path in reports.items()
        },
    }
    status_path = data_dir / "last_download.json"
    temp_path = data_dir / ".last_download.json.tmp"
    with temp_path.open("w", encoding="utf-8") as status_file:
        json.dump(status, status_file, ensure_ascii=False, indent=2)
        status_file.write("\n")
    os.replace(temp_path, status_path)
