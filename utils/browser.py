from __future__ import annotations

import logging
from contextlib import suppress

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from config import BrowserSettings
from models import LoadedPage


LOGGER = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self, settings: BrowserSettings, user_agent: str) -> None:
        self.settings = settings
        self.user_agent = user_agent
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def __enter__(self) -> "BrowserManager":
        self._playwright = sync_playwright().start()
        browser_type = getattr(self._playwright, self.settings.browser_name)
        launch_kwargs: dict[str, object] = {"headless": self.settings.headless}
        if self.settings.browser_name == "chromium" and self.settings.browser_channel:
            launch_kwargs["channel"] = self.settings.browser_channel
        self._browser = browser_type.launch(**launch_kwargs)
        self._context = self._browser.new_context(
            accept_downloads=True,
            ignore_https_errors=False,
            user_agent=self.user_agent,
            viewport={"width": 1440, "height": 1800},
        )
        self._context.set_default_navigation_timeout(self.settings.navigation_timeout_ms)
        self._context.set_default_timeout(self.settings.navigation_timeout_ms)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._context is not None:
            with suppress(PlaywrightError):
                self._context.close()
        if self._browser is not None:
            with suppress(PlaywrightError):
                self._browser.close()
        if self._playwright is not None:
            with suppress(PlaywrightError):
                self._playwright.stop()

    def new_page(self) -> Page:
        if self._context is None:
            raise RuntimeError("Browser context has not been initialized.")
        return self._context.new_page()

    def fetch_page(self, url: str, *, wait_until: str = "domcontentloaded") -> LoadedPage:
        page = self.new_page()
        try:
            page.goto(url, wait_until=wait_until)
            self.wait_for_readiness(page)
            return LoadedPage(
                url=page.url,
                html=page.content(),
                title=page.title(),
                via_browser=True,
            )
        finally:
            with suppress(PlaywrightError):
                page.close()

    def wait_for_readiness(self, page: Page) -> None:
        with suppress(PlaywrightTimeoutError):
            page.wait_for_load_state("networkidle", timeout=min(self.settings.navigation_timeout_ms, 5000))

    def safe_goto(self, page: Page, url: str) -> None:
        try:
            page.goto(url, wait_until="domcontentloaded")
            self.wait_for_readiness(page)
        except PlaywrightTimeoutError:
            LOGGER.warning("Timed out while navigating browser page to %s", url)
            raise
