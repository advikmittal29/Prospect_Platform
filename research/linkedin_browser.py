"""
LinkedIn browser  attaches to an existing Chrome session via CDP.

The research pipeline uses a human-logged-in Chrome profile so LinkedIn
pages load without auth walls. This module manages the lifecycle and
provides a stable Page reference to the tool classes.
"""
from __future__ import annotations

import logging
import subprocess
import time
import urllib.request
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from config import ChromeCDPConfig
from db import LinkedInCredentialORM, session_scope
from utils.logging import build_logger

logger = build_logger("prospect.research.browser")


def detect_linkedin_auth_screen(page: Page) -> Tuple[bool, str]:
    """
    Robust auth-wall detection for LinkedIn login/checkpoint pages.
    Returns (is_auth_screen, reason).
    """
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""

    score = 0
    reasons: List[str] = []

    auth_url_markers = (
        "/checkpoint/lg/login",
        "/checkpoint/challenge",
        "/uas/login",
        "/login",
        "/authwall",
    )
    if any(marker in url for marker in auth_url_markers):
        score += 3
        reasons.append("auth_url")

    selector_weights = [
        ("form.login__form", 2, "login_form"),
        ("input[name='session_key']", 2, "session_key"),
        ("input[name='session_password']", 2, "session_password"),
        ("button[data-litms-control-urn='login-submit']", 2, "submit_button"),
        ("div.alternate-signin-container", 1, "alternate_signin"),
        ("div.card-layout", 1, "card_layout"),
    ]
    for selector, weight, reason in selector_weights:
        try:
            if page.locator(selector).count() > 0:
                score += weight
                reasons.append(reason)
        except Exception:
            pass

    try:
        h1 = page.locator("h1.header__content__heading, h1").first
        text = (h1.inner_text(timeout=700) or "").strip().lower()
        if text == "sign in":
            score += 2
            reasons.append("sign_in_heading")
    except Exception:
        pass

    return (score >= 3, ",".join(dict.fromkeys(reasons)) or "no_auth_signals")


# ---------------------------------------------------------------------------
# Page state classification
# ---------------------------------------------------------------------------

class PageState(str, Enum):
    READY = "ready"
    PARTIALLY_READY = "partially_ready"
    POPUP_BLOCKING = "popup_blocking"
    AUTH_WALL = "auth_wall"
    INTERSTITIAL_BLOCKING = "interstitial_blocking"
    BROKEN = "broken"


@dataclass
class InteractionReport:
    state: PageState
    url: str
    title: str
    reason: str
    confidence: float
    blockers: List[str] = field(default_factory=list)
    actions_taken: List[str] = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        return self.state in {PageState.READY, PageState.PARTIALLY_READY}


# ---------------------------------------------------------------------------
# Chrome launcher (starts CDP-enabled Chrome if not already running)
# ---------------------------------------------------------------------------

class ChromeLauncher:
    def __init__(self, config: ChromeCDPConfig) -> None:
        self._cfg = config
        self._process: Optional[subprocess.Popen] = None

    def is_alive(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"{self._cfg.url}/json/version", timeout=2
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    def launch_if_needed(self) -> None:
        if self.is_alive():
            logger.debug("CDP already alive at %s", self._cfg.url)
            return

        exe = Path(self._cfg.exe)
        if not exe.exists():
            raise FileNotFoundError(f"Chrome not found: {exe}")

        udd = Path(self._cfg.user_data_dir)
        udd.mkdir(parents=True, exist_ok=True)

        port = int(self._cfg.url.rsplit(":", 1)[1])
        args = [
            str(exe),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={udd}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
        ]
        if self._cfg.headless:
            args.append("--headless=new")

        logger.info("Launching Chrome CDP on port %d ...", port)
        self._process = subprocess.Popen(args)
        self._wait_ready()

    def _wait_ready(self) -> None:
        deadline = time.time() + self._cfg.startup_wait_seconds + 10.0
        while time.time() < deadline:
            if self.is_alive():
                logger.info("Chrome CDP ready.")
                return
            time.sleep(0.4)
        raise RuntimeError(f"Chrome CDP did not come up at {self._cfg.url}")


# ---------------------------------------------------------------------------
# LinkedIn browser tool
# ---------------------------------------------------------------------------

class LinkedInBrowser:
    """
    Attaches to the existing Chrome via CDP.
    Provides goto_and_prepare() which navigates, handles popups,
    and returns a readiness report.
    """

    def __init__(self, config: ChromeCDPConfig) -> None:
        self._cfg = config
        self._pw: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._active_credential_id: Optional[int] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.browser:
            return
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.connect_over_cdp(self._cfg.url)

        contexts = self.browser.contexts
        self.context = contexts[0] if contexts else self.browser.new_context()
        self.context.set_default_timeout(self._cfg.action_timeout_ms)
        self.context.set_default_navigation_timeout(self._cfg.navigation_timeout_ms)

        pages = self.context.pages
        self.page = pages[0] if pages else self.context.new_page()
        logger.info("LinkedInBrowser attached to CDP at %s", self._cfg.url)

    def stop(self) -> None:
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        finally:
            self.browser = None
            self.context = None
            self.page = None
            if self._pw:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None
        logger.info("LinkedInBrowser disconnected.")

    def _require_started(self) -> None:
        if not self.browser or not self.page:
            raise RuntimeError("LinkedInBrowser.start() must be called first.")

    # ------------------------------------------------------------------
    # Navigation + readiness
    # ------------------------------------------------------------------

    def goto_and_prepare(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
    ) -> InteractionReport:
        self._require_started()
        assert self.page is not None
        actions: List[str] = []

        try:
            self.page.goto(url, wait_until=wait_until,
                           timeout=self._cfg.navigation_timeout_ms)
            actions.append(f"goto:{url}")
        except PlaywrightTimeoutError:
            actions.append("goto_timeout")
        except PlaywrightError as exc:
            return InteractionReport(
                state=PageState.BROKEN,
                url=self.page.url,
                title="",
                reason=f"Navigation error: {type(exc).__name__}",
                confidence=0.95,
                blockers=["navigation_error"],
                actions_taken=actions,
            )

        self._stabilize()
        actions.append("stabilized")

        report = self._classify()
        report.actions_taken.extend(actions)
        if report.state == PageState.AUTH_WALL:
            if self.ensure_logged_in(force=True):
                self._stabilize()
                report = self._classify()
                report.actions_taken.extend(actions + ["auto_login"])

        for attempt in range(self._cfg.recovery_attempts):
            if report.state not in {PageState.POPUP_BLOCKING, PageState.INTERSTITIAL_BLOCKING}:
                break
            recovered = self._try_recover()
            if not recovered:
                break
            actions.append(f"recovery:{attempt + 1}")
            self._stabilize()
            follow = self._classify()
            follow.actions_taken = report.actions_taken + follow.actions_taken
            report = follow

        return report

    # ------------------------------------------------------------------
    # Auth handling
    # ------------------------------------------------------------------

    def ensure_logged_in(self, force: bool = False) -> bool:
        """
        Ensure LinkedIn session is authenticated.
        If auth screen is detected, logs in using DB-stored credentials.
        """
        self._require_started()
        assert self.page is not None

        is_auth, reason = detect_linkedin_auth_screen(self.page)
        if not force and not is_auth:
            return True

        cred = self._load_active_credential()
        if cred is None:
            raise RuntimeError(
                "LinkedIn auth required but no active credentials found in table "
                "'linkedin_credentials'."
            )

        self._active_credential_id = cred.id
        self._record_login_attempt(cred.id, success=None, failure_reason=None)
        logger.warning("LinkedIn auth detected (%s). Attempting DB-backed login.", reason)

        ok = self._perform_login(cred.email, cred.password)
        if ok:
            self._record_login_attempt(cred.id, success=True, failure_reason=None)
            logger.info("LinkedIn login successful for %s", cred.email)
            return True

        self._record_login_attempt(
            cred.id,
            success=False,
            failure_reason="login_form_submit_did_not_clear_authwall",
        )
        raise RuntimeError("LinkedIn login failed after form submission.")

    def _load_active_credential(self) -> Optional[LinkedInCredentialORM]:
        with session_scope() as session:
            row = (
                session.query(LinkedInCredentialORM)
                .filter(LinkedInCredentialORM.active == True)
                .order_by(LinkedInCredentialORM.priority.asc(), LinkedInCredentialORM.id.asc())
                .first()
            )
            if row:
                session.expunge(row)
            return row

    def _record_login_attempt(
        self,
        credential_id: int,
        *,
        success: Optional[bool],
        failure_reason: Optional[str],
    ) -> None:
        with session_scope() as session:
            row = session.query(LinkedInCredentialORM).filter_by(id=credential_id).one_or_none()
            if not row:
                return
            now_dt = datetime.utcnow()
            row.last_login_attempt_utc = now_dt
            row.updated_at_utc = now_dt
            if success is True:
                row.last_login_success_utc = now_dt
                row.last_login_failure_reason = None
            elif success is False:
                row.last_login_failure_reason = (failure_reason or "unknown")[:1000]

    def _perform_login(self, email: str, password: str) -> bool:
        assert self.page is not None

        # If not currently on login UI, route to login and continue.
        is_auth, _ = detect_linkedin_auth_screen(self.page)
        if not is_auth:
            try:
                self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=20000)
                self._stabilize()
            except Exception:
                pass

        username = self._first_visible_locator([
            "input[name='session_key']",
            "#username",
            "form.login__form input[autocomplete*='username']",
            "input[aria-label*='Email' i]",
            "input[type='email']",
        ])
        password_input = self._first_visible_locator([
            "input[name='session_password']",
            "#password",
            "form.login__form input[type='password']",
            "input[autocomplete='current-password']",
        ])
        if username is None or password_input is None:
            return False

        username.fill(email, timeout=5000)
        password_input.fill(password, timeout=5000)

        keep_me = self._first_visible_locator([
            "#rememberMeOptIn-checkbox",
            "input[name='rememberMeOptIn'][type='checkbox']",
            "input[type='checkbox'][name*='remember']",
        ])
        if keep_me is not None:
            try:
                if not keep_me.is_checked():
                    keep_me.check(force=True)
            except Exception:
                pass

        submit = self._first_visible_locator([
            "form.login__form button[type='submit']",
            "button[data-litms-control-urn='login-submit']",
            "button[aria-label='Sign in']",
            "button:has-text('Sign in')",
        ])
        if submit is not None:
            submit.click(timeout=6000)
        else:
            password_input.press("Enter")

        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        self.page.wait_for_timeout(1800)

        still_auth, _ = detect_linkedin_auth_screen(self.page)
        return not still_auth

    def _first_visible_locator(self, selectors: List[str]):
        assert self.page is not None
        for selector in selectors:
            try:
                loc = self.page.locator(selector)
                if loc.count() == 0:
                    continue
                for idx in range(min(loc.count(), 3)):
                    item = loc.nth(idx)
                    try:
                        if item.is_visible(timeout=500):
                            return item
                    except Exception:
                        continue
            except Exception:
                continue
        return None

    def _stabilize(self) -> None:
        assert self.page is not None
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        try:
            self.page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass
        self.page.wait_for_timeout(700)

    def _try_recover(self) -> bool:
        assert self.page is not None
        recovered = False
        close_sels = [
            'button[aria-label*="Close" i]',
            'button[aria-label*="Dismiss" i]',
            'button[aria-label*="No thanks" i]',
            'button[aria-label*="Not now" i]',
            'button[aria-label*="Skip" i]',
            '[role="dialog"] button[aria-label]',
        ]
        for sel in close_sels:
            try:
                loc = self.page.locator(sel)
                for i in range(min(loc.count(), 3)):
                    item = loc.nth(i)
                    if not item.is_visible(timeout=300):
                        continue
                    label = (
                        (item.get_attribute("aria-label") or "")
                        + " "
                        + (item.inner_text(timeout=300) or "")
                    ).strip().lower()
                    if any(x in label for x in ["sign in", "log in", "join", "sign up"]):
                        continue
                    item.click(timeout=1200)
                    self.page.wait_for_timeout(350)
                    recovered = True
            except Exception:
                pass
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(150)
        except Exception:
            pass
        return recovered

    # ------------------------------------------------------------------
    # DOM classification
    # ------------------------------------------------------------------

    def _classify(self) -> InteractionReport:
        assert self.page is not None
        try:
            signals = self.page.evaluate(self._SIGNALS_JS)
        except Exception:
            signals = {}

        state, reason, blockers, conf = self._interpret(signals)
        try:
            title = self.page.title()
        except Exception:
            title = ""

        return InteractionReport(
            state=state,
            url=self.page.url,
            title=title,
            reason=reason,
            confidence=conf,
            blockers=blockers,
            signals=signals,
        )

    @staticmethod
    def _interpret(signals: Dict[str, Any]):
        url = (signals.get("url") or "").lower()
        dialog_count = int(signals.get("dialogCount") or 0)
        pw_count = int(signals.get("passwordFieldCount") or 0)
        auth_inputs = int(signals.get("authInputCount") or 0)
        overlay_count = int(signals.get("fixedLargeOverlayCount") or 0)
        visible_main = bool(signals.get("visibleMain"))
        main_ratio = float(signals.get("mainRatio") or 0.0)
        body_locked = bool(signals.get("bodyOverflowHidden"))
        has_inert = bool(signals.get("hasInertNodes"))
        center_text = (signals.get("centerText") or "").lower()
        buttons = signals.get("buttons") or []

        blockers: List[str] = []
        auth_text_hits = sum(
            1 for b in buttons[:60]
            if (b.get("text") or "").strip().lower() in
               {"sign in", "log in", "login", "join now", "sign up"}
        )
        url_auth = any(p in url for p in ["/login", "/signup", "/authwall", "/checkpoint"])

        auth_score = 0
        if url_auth:
            auth_score += 4; blockers.append("auth_url")
        if pw_count > 0:
            auth_score += 4; blockers.append("password_field")
        if auth_inputs >= 2:
            auth_score += 2; blockers.append("auth_form")
        if dialog_count > 0 and overlay_count > 0:
            auth_score += 2; blockers.append("modal_overlay")
        if body_locked or has_inert:
            auth_score += 1; blockers.append("background_locked")
        if auth_text_hits >= 1:
            auth_score += 1; blockers.append("auth_cta")
        if "sign in" in center_text or "join now" in center_text:
            auth_score += 1; blockers.append("auth_centered")

        popup_score = 0
        if overlay_count > 0:
            popup_score += 3; blockers.append("large_overlay")
        if dialog_count > 0:
            popup_score += 2; blockers.append("dialog")
        if body_locked:
            popup_score += 1; blockers.append("scroll_locked")
        if has_inert:
            popup_score += 1; blockers.append("inert_bg")

        if auth_score >= 6:
            return (PageState.AUTH_WALL,
                    "Page blocked by authentication wall.",
                    list(dict.fromkeys(blockers)),
                    min(0.99, 0.60 + auth_score * 0.05))

        if popup_score >= 4 and not (visible_main and main_ratio > 0.18):
            return (PageState.POPUP_BLOCKING,
                    "Modal or popup is blocking interaction.",
                    list(dict.fromkeys(blockers)),
                    min(0.95, 0.55 + popup_score * 0.06))

        if visible_main and main_ratio > 0.10:
            if overlay_count > 0 or dialog_count > 0:
                return (PageState.PARTIALLY_READY,
                        "Main content visible, minor UI interference.",
                        list(dict.fromkeys(blockers)), 0.72)
            return (PageState.READY,
                    "Main content visible, no major blockers.",
                    [], 0.92)

        if overlay_count > 0 or dialog_count > 0:
            return (PageState.INTERSTITIAL_BLOCKING,
                    "Interstitial or unknown blocker detected.",
                    list(dict.fromkeys(blockers)), 0.75)

        return (PageState.BROKEN,
                "No interactive main content detected.",
                list(dict.fromkeys(blockers)), 0.70)

    _SIGNALS_JS = """
    () => {
      const vpW = window.innerWidth || 0, vpH = window.innerHeight || 0;
      const vpArea = Math.max(1, vpW * vpH);
      function isVisible(el) {
        if (!el) return false;
        const s = getComputedStyle(el), r = el.getBoundingClientRect();
        return s.display!=='none' && s.visibility!=='hidden' &&
               parseFloat(s.opacity||'1')>0.05 && r.width>4 && r.height>4;
      }
      function ratio(r) {
        const w=Math.max(0,Math.min(r.right,vpW)-Math.max(r.left,0));
        const h=Math.max(0,Math.min(r.bottom,vpH)-Math.max(r.top,0));
        return (w*h)/vpArea;
      }
      const dialogs=[...document.querySelectorAll('[role="dialog"],[aria-modal="true"],dialog')].filter(isVisible);
      const pwFields=[...document.querySelectorAll('input[type="password"]')].filter(isVisible);
      const authInputs=[...document.querySelectorAll('input[type="email"],input[type="password"],input[autocomplete="username"]')].filter(isVisible);
      const overlays=[...document.querySelectorAll('body *')].filter(isVisible)
        .map(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);
          return{ratio:ratio(r),role:el.getAttribute('role'),
                 ariaModal:el.getAttribute('aria-modal'),
                 position:s.position,pointerEvents:s.pointerEvents};})
        .filter(x=>(x.position==='fixed'||x.role==='dialog'||x.ariaModal==='true')&&x.ratio>=0.30&&x.pointerEvents!=='none');
      const main=document.querySelector('main,[role="main"],#main');
      const mainR=main?main.getBoundingClientRect():null;
      const center=document.elementFromPoint(vpW/2,vpH/2);
      const buttons=[...document.querySelectorAll('button,a,[role="button"]')].filter(isVisible).slice(0,200)
        .map(el=>({text:(el.innerText||el.getAttribute('aria-label')||'').trim().slice(0,80)}));
      const bs=getComputedStyle(document.body),hs=getComputedStyle(document.documentElement);
      return{
        url:location.href,title:document.title,
        visibleMain:!!(main&&isVisible(main)),
        mainRatio:mainR?ratio(mainR):0,
        dialogCount:dialogs.length,
        passwordFieldCount:pwFields.length,
        authInputCount:authInputs.length,
        fixedLargeOverlayCount:overlays.length,
        bodyOverflowHidden:bs.overflow==='hidden'||hs.overflow==='hidden',
        hasInertNodes:document.querySelectorAll('[inert]').length>0,
        centerText:center?(center.innerText||center.getAttribute('aria-label')||'').trim().slice(0,120):'',
        buttons
      };
    }
    """


