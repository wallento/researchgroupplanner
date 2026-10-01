import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from sap_integration.workbooks import same_nonempty_workbook_content


class WuerzburgWebGUIBackend:
    """Downloader for the SAP WebGUI layout used at Würzburg University."""

    MAX_REPORT_DOWNLOAD_ATTEMPTS = 3

    REPORT_BUTTONS = {
        "budget": "M0:46:1::0:1-title",
        "actual": "M0:46:1::0:2-title",
        "commitments": "M0:46:1::0:3-title",
    }

    def __init__(self, config):
        self.config = config

    def download(self, year, download_dir):
        download_dir = Path(download_dir).resolve()
        driver = self._create_driver(download_dir)
        wait = WebDriverWait(driver, self.config.timeout)

        try:
            self._open_report(driver, wait, year)
            reports = {}
            for report_name, button_id in self.REPORT_BUTTONS.items():
                for attempt in range(self.MAX_REPORT_DOWNLOAD_ATTEMPTS):
                    report_path = self._download_report(
                        driver,
                        wait,
                        download_dir,
                        report_name,
                        button_id,
                    )
                    if not self._is_stale_transaction_export(
                        report_name,
                        report_path,
                        reports,
                    ):
                        reports[report_name] = report_path
                        break

                    report_path.unlink()
                    if attempt == self.MAX_REPORT_DOWNLOAD_ATTEMPTS - 1:
                        raise ValueError(
                            "SAP hat für Ist und Obligo denselben nicht-leeren "
                            "Export geliefert. Der Bericht wurde beim Umschalten "
                            "nicht rechtzeitig aktualisiert."
                        )
                    time.sleep(max(self.config.action_delay, 2) * (attempt + 1))
            return reports
        finally:
            driver.quit()

    def _create_driver(self, download_dir):
        if self.config.browser == "firefox":
            options = webdriver.FirefoxOptions()
            if self.config.headless:
                options.add_argument("--headless")
            options.set_preference("intl.accept_languages", "de")
            options.set_preference("browser.download.folderList", 2)
            options.set_preference("browser.download.dir", str(download_dir))
            options.set_preference("browser.download.useDownloadDir", True)
            options.set_preference(
                "browser.helperApps.neverAsk.saveToDisk",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream",
            )
            if self.config.browser_binary:
                options.binary_location = self.config.browser_binary
            return webdriver.Firefox(options=options)

        options = webdriver.ChromeOptions()
        if self.config.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--lang=de")
        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
            },
        )
        if self.config.browser_binary:
            options.binary_location = self.config.browser_binary
        return webdriver.Chrome(options=options)

    def _open_report(self, driver, wait, year):
        driver.get(self.config.url)
        self._fill(wait, By.NAME, "sap-user", self.config.user)
        self._fill(
            wait,
            By.NAME,
            "sap-password",
            self.config.password + Keys.RETURN,
        )

        self._double_click(driver, wait, "tree#C105#7#1#1#i")
        self._double_click(driver, wait, "tree#C105#9#1#1#i")
        self._fill(wait, By.ID, "M0:46:::6:33", self.config.finanzstelle, submit=True)
        self._fill(wait, By.ID, "M0:46:::13:34", str(year), clear=True, submit=True)
        self._fill(wait, By.ID, "M0:46:::13:59", str(year), clear=True, submit=True)
        self._click(driver, wait, By.ID, "M0:37::btn[8]")
        time.sleep(max(self.config.action_delay, 10))

    def _download_report(
        self,
        driver,
        wait,
        download_dir,
        report_name,
        button_id,
    ):
        known_files = set(download_dir.iterdir())
        self._click(driver, wait, By.ID, button_id)
        # SAP updates the report asynchronously. The toolbar remains clickable
        # while it still points at the preceding report, so allow the table to
        # settle before opening the export dialog.
        time.sleep(max(self.config.action_delay, 1))
        self._click(driver, wait, By.XPATH, "//*[contains(@id, '_MB_EXPORT')]")
        self._click(
            driver,
            wait,
            By.XPATH,
            "//*[contains(@aria-label, 'Tabellenkalkulation')]",
        )
        self._click(driver, wait, By.ID, "M1:37::btn[0]")
        self._click(driver, wait, By.ID, "UpDownDialogChoose")

        exported_file = self._wait_for_download(download_dir, known_files)
        destination = download_dir / f"{report_name}.xlsx"
        exported_file.replace(destination)
        return destination

    @staticmethod
    def _is_stale_transaction_export(report_name, report_path, reports):
        return (
            report_name == "commitments"
            and "actual" in reports
            and same_nonempty_workbook_content(reports["actual"], report_path)
        )

    def _wait_for_download(self, download_dir, known_files):
        deadline = time.monotonic() + self.config.timeout
        last_candidate = None
        last_size = None

        while time.monotonic() < deadline:
            partial_downloads = list(download_dir.glob("*.crdownload"))
            partial_downloads += list(download_dir.glob("*.part"))
            candidates = [
                path
                for path in download_dir.glob("*.xlsx")
                if path not in known_files and path.is_file()
            ]
            if candidates and not partial_downloads:
                candidate = max(candidates, key=lambda path: path.stat().st_mtime)
                size = candidate.stat().st_size
                if candidate == last_candidate and size == last_size and size > 0:
                    return candidate
                last_candidate = candidate
                last_size = size
            time.sleep(0.5)

        raise TimeoutError("Der SAP-Excel-Download wurde nicht rechtzeitig abgeschlossen.")

    def _fill(self, wait, by, value, text, clear=False, submit=False):
        element = wait.until(EC.element_to_be_clickable((by, value)))
        if clear:
            element.clear()
        element.send_keys(text)
        if submit:
            element.send_keys(Keys.RETURN)
        time.sleep(self.config.action_delay)

    def _click(self, driver, wait, by, value):
        def click_current_element():
            element = wait.until(EC.element_to_be_clickable((by, value)))
            ActionChains(driver).click(element).perform()

        self._retry_stale(click_current_element)
        time.sleep(self.config.action_delay)

    def _double_click(self, driver, wait, element_id):
        def double_click_current_element():
            element = wait.until(EC.element_to_be_clickable((By.ID, element_id)))
            ActionChains(driver).double_click(element).perform()

        # The SAP tree is rebuilt after interaction. Resolve the element again
        # for the second double-click instead of retaining a stale DOM reference.
        self._retry_stale(double_click_current_element)
        self._retry_stale(double_click_current_element)
        time.sleep(self.config.action_delay)

    @staticmethod
    def _retry_stale(action, attempts=3):
        for attempt in range(attempts):
            try:
                return action()
            except StaleElementReferenceException:
                if attempt == attempts - 1:
                    raise
