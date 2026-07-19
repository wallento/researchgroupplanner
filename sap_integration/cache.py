import json
from decimal import Decimal
from pathlib import Path

from sap_integration.parser import SCHEMA_VERSION


class SAPCacheError(ValueError):
    pass


def available_years(data_dir):
    processed_dir = Path(data_dir) / "processed"
    years = []
    if processed_dir.is_dir():
        for path in processed_dir.glob("*.json"):
            if path.stem.isdigit():
                years.append(int(path.stem))
    return sorted(years, reverse=True)


def load_year(data_dir, year):
    cache_path = Path(data_dir) / "processed" / f"{year}.json"
    if not cache_path.is_file():
        raise SAPCacheError(f"Für {year} liegen noch keine aufbereiteten SAP-Daten vor.")
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SAPCacheError(f"Der SAP-Cache für {year} ist beschädigt.") from error
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("year") != year:
        raise SAPCacheError(f"Der SAP-Cache für {year} hat ein unbekanntes Format.")
    return payload


def fund_values(cached_fund):
    if cached_fund is None:
        return None
    result = dict(cached_fund)
    for key in (
        "budget",
        "actual_total",
        "commitments_total",
        "combined_total",
        "remaining",
    ):
        result[key] = Decimal(result[key]) if result.get(key) is not None else None
    result["transactions"] = [
        {**transaction, "amount": Decimal(transaction["amount"])}
        for transaction in result.get("transactions", [])
    ]
    return result
