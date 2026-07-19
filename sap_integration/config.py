from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


class SAPConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class SAPConfig:
    enabled: bool
    url: str
    user: str
    password: str
    finanzstelle: str
    data_dir: Path
    browser: str
    browser_binary: str
    headless: bool
    timeout: int
    action_delay: float
    backend: str

    @classmethod
    def from_settings(cls):
        config = cls(
            enabled=settings.SAP_ENABLED,
            url=settings.SAP_URL.strip(),
            user=settings.SAP_USER.strip(),
            password=settings.SAP_PASSWORD,
            finanzstelle=settings.SAP_FINANZSTELLE.strip(),
            data_dir=Path(settings.SAP_DATA_DIR),
            browser=settings.SAP_BROWSER.strip().lower(),
            browser_binary=settings.SAP_BROWSER_BINARY.strip(),
            headless=settings.SAP_HEADLESS,
            timeout=settings.SAP_TIMEOUT,
            action_delay=settings.SAP_ACTION_DELAY,
            backend=settings.SAP_BACKEND.strip(),
        )
        config.validate()
        return config

    def validate(self):
        if not self.enabled:
            raise SAPConfigurationError(
                "Die SAP-Integration ist deaktiviert. SAP_ENABLED muss aktiviert sein."
            )

        required = {
            "SAP_URL": self.url,
            "SAP_USER": self.user,
            "SAP_PASSWORD": self.password,
            "SAP_FINANZSTELLE": self.finanzstelle,
            "SAP_BACKEND": self.backend,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise SAPConfigurationError(
                f"Fehlende SAP-Konfiguration: {', '.join(missing)}"
            )
        if self.browser not in {"chrome", "firefox"}:
            raise SAPConfigurationError(
                "SAP_BROWSER muss 'chrome' oder 'firefox' sein."
            )
        if self.timeout <= 0:
            raise SAPConfigurationError("SAP_TIMEOUT muss größer als 0 sein.")
        if self.action_delay < 0:
            raise SAPConfigurationError("SAP_ACTION_DELAY darf nicht negativ sein.")
