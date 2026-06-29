"""
Playwright browser wrapper used exclusively by the Naukri ingestion pipeline.
Launches its own Chromium instance (not CDP-attached) so the ingestion job
can run headless on the scheduler without touching the shared LinkedIn browser.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, List, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from config import BrowserConfig, NaukriConfig

logger = logging.getLogger("prospect.jobs.browser")


# ---------------------------------------------------------------------------
# Thin page handle  keeps callers free of raw Playwright imports
# ---------------------------------------------------------------------------

@dataclass
class PageHandle:
    page: Page

    def text(self, selector: str, default: Optional[str] = None) -> Optional[str]:
        loc = self.page.locator(selector)
        try:
            if loc.count() == 0:
                return default
            return loc.first.inner_text(timeout=3000).strip() or default
        except Exception:
            return default

    def texts(self, selector: str) -> List[str]:
        loc = self.page.locator(selector)
        out: List[str] = []
        try:
            for i in range(loc.count()):
                try:
                    t = loc.nth(i).inner_text(timeout=2000).strip()
                    if t:
                        out.append(t)
                except Exception:
                    pass
        except Exception:
            pass
        return out

    def attr(self, selector: str, name: str, default: Optional[str] = None) -> Optional[str]:
        loc = self.page.locator(selector)
        try:
            if loc.count() == 0:
                return default
            return loc.first.get_attribute(name) or default
        except Exception:
            return default

    def inner_html(self, selector: str, default: Optional[str] = None) -> Optional[str]:
        loc = self.page.locator(selector)
        try:
            if loc.count() == 0:
                return default
            return loc.first.inner_html(timeout=3000) or default
        except Exception:
            return default

    def full_html(self) -> str:
        return self.page.content()

    def evaluate(self, expression: str):
        return self.page.evaluate(expression)


# ---------------------------------------------------------------------------
# Browser wrapper
# ---------------------------------------------------------------------------

class NaukriBrowser:
    """
    Manages a Playwright Chromium session for Naukri scraping.
    Designed for single-threaded sequential use; call start() before use
    and close() when done.
    """

    def __init__(self, browser_cfg: BrowserConfig, naukri_cfg: NaukriConfig) -> None:
        self._bcfg = browser_cfg
        self._ncfg = naukri_cfg
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._browser:
            return
        self._pw = sync_playwright().start()
        launch_opts = dict(
            headless=self._bcfg.headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        self._browser = self._pw.chromium.launch(**launch_opts)
        self._context = self._browser.new_context(
            viewport={
                "width": self._bcfg.viewport_width,
                "height": self._bcfg.viewport_height,
            },
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            java_script_enabled=True,
        )
        self._context.set_default_timeout(self._bcfg.action_timeout_ms)
        self._context.set_default_navigation_timeout(self._bcfg.nav_timeout_ms)
        self._page = self._context.new_page()
        logger.info("NaukriBrowser started (headless=%s)", self._bcfg.headless)

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        finally:
            self._browser = None
            self._context = None
            self._page = None
            if self._pw:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None
        logger.info("NaukriBrowser closed.")

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("NaukriBrowser not started. Call start() first.")
        return self._page

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def safe_visit(self, url: str) -> PageHandle:
        """Navigate to URL, close any popup, return PageHandle."""
        for attempt in range(1, 3):
            try:
                self.page.goto(url, wait_until="domcontentloaded",
                               timeout=self._bcfg.nav_timeout_ms)
                break
            except PlaywrightTimeoutError:
                logger.warning("Navigation timeout on attempt %d for %s", attempt, url)
                if attempt == 2:
                    logger.error("Giving up on %s after 2 attempts", url)
        self._close_popups()
        time.sleep(self._bcfg.polite_sleep_sec)
        return PageHandle(self.page)

    def intelligent_scroll(self, rounds: Optional[int] = None) -> None:
        """Scroll down the listing page to load lazy cards."""
        rounds = rounds or self._bcfg.listing_scroll_rounds
        for _ in range(rounds):
            try:
                self.page.evaluate("window.scrollBy(0, window.innerHeight * 0.85)")
                time.sleep(self._bcfg.scroll_pause_sec)
            except Exception:
                break

    # ------------------------------------------------------------------
    # Popup handling
    # ------------------------------------------------------------------

    def _close_popups(self) -> None:
        for sel in self._ncfg.popup_close_selectors:
            try:
                loc = self.page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible(timeout=800):
                    loc.first.click(timeout=1200)
                    time.sleep(0.15)
            except Exception:
                pass
        # Also try Escape to dismiss any remaining overlay
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass


