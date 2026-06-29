"""
LinkedIn Outreach Sender – automated connect/message dispatch.

Process flow:
    FETCH → LOCK → INCREMENT ATTEMPT → LOAD PROFILE → DECIDE ACTION
         → EXECUTE ACTION → UPDATE STATUS → UNLOCK

=== SELECTOR STRATEGY (grounded in real LinkedIn DOM) ===

CONNECTION STATE DETECTION  (multi-signal scoring):
  Connected  (1st degree):
    - Degree badge text contains "1st"
    - NO Follow button visible (aria-label="Follow …")
    - Message link present with href containing /messaging/compose/ AND interop=msgOverlay
  Not connected (2nd/3rd):
    - Degree badge text contains "2nd" or "3rd" (or absent)
    - Follow button IS visible (aria-label="Follow [NAME]")
    - Connect entry appears in More-menu popover
  Pending:
    - Button with aria-label containing "Pending" visible on top-card
    - OR More-menu has a "Withdraw" item

CONNECT FLOW (not-connected):
  1. Click "More" button  (aria-label="More", aria-expanded attribute)
  2. Popover role="menu" appears
  3. Click menuitem: <a href="/preload/custom-invite/..."> whose inner div
     has aria-label="Invite … to connect"  (SVG id="connect-small")
  4. Modal appears: class="artdeco-modal__actionbar"
     - "Add a note"       button: aria-label="Add a note"
     - "Send without a note" button: aria-label="Send without a note"
  5. If note provided -> click "Add a note" -> textarea appears:
     class="connect-button-send-invite__custom-message", name="message"
     -> fill text -> click aria-label="Send invitation" button
  6. If no note -> click "Send without a note"

MESSAGE FLOW (connected):
  1. Click the Message link:
     <a href="/messaging/compose/?...&interop=msgOverlay"> (SVG id="send-privately-medium")
  2. Compose overlay opens:
     div class="msg-form__contenteditable", role="textbox", aria-label="Write a message..."
  3. Fill text
  4. Click send: <button class="msg-form__send-button" type="submit">
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from config import AppConfig
from db import ProspectORM, session_scope
from research.linkedin_browser import LinkedInBrowser
from utils.logging import build_logger

logger = build_logger("prospect.outreach.linkedin_sender")


# ---------------------------------------------------------------------------
# Enums / result types
# ---------------------------------------------------------------------------

class OutreachAction(str, Enum):
    CONNECT = "CONNECT"
    MESSAGE = "MESSAGE"
    SKIP    = "SKIP"
    UNKNOWN = "UNKNOWN"


@dataclass
class ConnectionState:
    verdict: str          # "connected" | "not_connected" | "pending" | "unknown"
    confidence: int       # 0-100
    signals: List[str] = field(default_factory=list)


@dataclass
class OutreachResult:
    prospect_id: int
    action: OutreachAction
    success: bool
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Selector constants  (stable attributes only – no dynamic class hashes)
#
# Every selector below is derived from real LinkedIn DOM snapshots.
# Ordered from most-specific/stable to least-specific fallback.
# ---------------------------------------------------------------------------

# Degree badge – the <p> element that holds "· 1st", "· 2nd", "· 3rd"
_DEGREE_1ST     = "p:has-text('· 1st')"
_DEGREE_2ND     = "p:has-text('· 2nd')"
_DEGREE_3RD     = "p:has-text('· 3rd')"

# Follow button – only present when NOT connected
# Real DOM: <button aria-label="Follow AJAY SINGH">
_FOLLOW_BTN_SEL = "button[aria-label^='Follow ']"

# Pending button – request already sent
_PENDING_BTN_SEL = "button[aria-label='Pending'], button[aria-label^='Pending,']"

# Message link – present on both connected AND non-connected profiles
# Real DOM: <a href="/messaging/compose/?profileUrn=...&interop=msgOverlay">
_MSG_LINK_SEL = (
    "a[href*='/messaging/compose/'][href*='interop=msgOverlay'], "
    "a[href*='/messaging/compose/'], "
    "button[aria-label*='Message']"
)

# "More" overflow button on the top-card actions row
# Real DOM: <button aria-label="More" aria-expanded="false">
_MORE_BTN_SEL = "button[aria-label='More'][aria-expanded]"

# More-menu popover container
_MORE_MENU_SEL = "[role='menu']"

# Connect entry inside the More-menu
# Real DOM: <a href="/preload/custom-invite/?vanityName=...">
#             <div aria-label="Invite AJAY SINGH to connect">
#               <svg id="connect-small"> <p>Connect</p>
_MENU_CONNECT_HREF_SEL = "a[href*='/preload/custom-invite/']"
_MENU_CONNECT_ARIA_SEL = "[aria-label*='to connect']"
_MENU_CONNECT_TEXT_SEL = "[role='menu'] p:has-text('Connect')"

# Withdraw entry in More-menu (indicates pending request)
_MENU_WITHDRAW_SEL = (
    "[role='menu'] [aria-label*='Withdraw'], "
    "[role='menu'] p:has-text('Withdraw')"
)

# Connect modal – the artdeco modal that pops up after clicking Connect
_MODAL_ACTIONBAR_SEL = ".artdeco-modal__actionbar"
_ADD_NOTE_BTN_SEL    = "button[aria-label='Add a note']"
_SKIP_NOTE_BTN_SEL   = "button[aria-label='Send without a note']"

# Note textarea inside the connect modal
# Real DOM: <div class="connect-button-send-invite__custom-message-box">
#             <textarea name="message" id="custom-message"
#               class="connect-button-send-invite__custom-message ...">
_NOTE_TEXTAREA_SELS = [
    "textarea.connect-button-send-invite__custom-message",
    "textarea[name='message'][id='custom-message']",
    "div.connect-button-send-invite__custom-message-box textarea",
]

# Send invitation button (appears after filling the note textarea)
# Real DOM: <button aria-label="Send invitation" class="artdeco-button--primary">
_SEND_INVITE_BTN_SEL = "button[aria-label='Send invitation']"

# Message compose area (opens as an overlay after clicking the Message link)
# Real DOM: <div class="msg-form__contenteditable" role="textbox"
#                aria-label="Write a message..." contenteditable="true">
_MSG_COMPOSE_SELS = [
    "div.msg-form__contenteditable[role='textbox']",
    "div[role='textbox'][aria-label='Write a message\u2026']",
    "div[contenteditable='true'][aria-label='Write a message\u2026']",
    "div.msg-form__contenteditable",
]

# Message send button
# Real DOM: <button class="msg-form__send-button artdeco-button--1" type="submit">
_MSG_SEND_BTN_SELS = [
    "button.msg-form__send-button[type='submit']",
    "button.msg-form__send-button",
]


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------

class LinkedInOutreachSender:
    """
    Orchestrates the full outreach lifecycle for a batch of prospects.

    Guarantees:
      - A prospect with outreach_sent=TRUE is never re-processed.
      - The in-progress lock is ALWAYS released (try/finally).
      - Failed records are only retried while outreach_attempts < MAX_ATTEMPTS.
    """

    def __init__(
        self,
        browser: LinkedInBrowser,
        config: AppConfig,
        agent_id: Optional[int] = None,
    ) -> None:
        self._browser      = browser
        self._cfg          = config
        self._agent_id     = agent_id

        li_cfg             = config.linkedin_outreach
        self._batch_size   = li_cfg.batch_size
        self._max_attempts = li_cfg.max_attempts
        self._delay_min    = li_cfg.delay_min_seconds
        self._delay_max    = li_cfg.delay_max_seconds
        self._connect_note = li_cfg.connect_note

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> dict:
        stats = dict(processed=0, sent_connect=0, sent_message=0, skipped=0, failed=0)
        prospects = self._fetch_batch()

        if not prospects:
            logger.info("LinkedInOutreachSender: no eligible prospects.")
            return stats

        logger.info(
            "LinkedInOutreachSender: processing %d prospect(s) "
            "(max_attempts=%d, batch=%d).",
            len(prospects), self._max_attempts, self._batch_size,
        )

        for prospect in prospects:
            stats["processed"] += 1
            result = self._process_one(prospect)

            if result.success:
                if result.action == OutreachAction.CONNECT:
                    stats["sent_connect"] += 1
                elif result.action == OutreachAction.MESSAGE:
                    stats["sent_message"] += 1
                else:
                    stats["skipped"] += 1
            else:
                if result.action == OutreachAction.SKIP:
                    stats["skipped"] += 1
                else:
                    stats["failed"] += 1

            self._human_delay()

        return stats

    # ------------------------------------------------------------------
    # Test entry point  (CLI / ad-hoc, no DB reads or writes)
    # ------------------------------------------------------------------

    def run_test(
        self,
        url: str,
        message: Optional[str] = None,
        force_action: str = "auto",
    ) -> OutreachResult:
        """
        Execute outreach against a single LinkedIn profile URL without
        touching the database at all.  Intended for CLI testing only.

        Parameters
        ----------
        url:
            Full LinkedIn profile URL, e.g.
            "https://www.linkedin.com/in/someone/"
        message:
            Text to send as a direct message or use as a connection note.
            May be None (connect request will be sent without a note).
        force_action:
            "auto"    - detect connection state and act accordingly
            "connect" - always attempt a connect request (with note if message provided)
            "message" - always attempt to send a direct message

        Returns
        -------
        OutreachResult with prospect_id=-1 (no DB row).
        """
        _DUMMY_ID = -1

        url = (url or "").strip()
        if not url:
            return OutreachResult(
                prospect_id=_DUMMY_ID,
                action=OutreachAction.UNKNOWN,
                success=False,
                error="No URL provided.",
            )

        logger.info("[TEST] Navigating to: %s", url)
        report = self._browser.goto_and_prepare(url)

        if not report.is_usable:
            return OutreachResult(
                prospect_id=_DUMMY_ID,
                action=OutreachAction.UNKNOWN,
                success=False,
                error=f"Page not usable: {report.state} / {report.reason}",
            )

        self._wait_for_actions_ready()

        # Determine action
        if force_action == "auto":
            conn_state = self._detect_connection_state()
            logger.info(
                "[TEST] Connection state: verdict=%s confidence=%d signals=%s",
                conn_state.verdict, conn_state.confidence, conn_state.signals,
            )

            class _Stub:
                id = _DUMMY_ID

            action = self._decide_action(conn_state, _Stub())  # type: ignore[arg-type]

        elif force_action == "connect":
            action = OutreachAction.CONNECT
            logger.info("[TEST] Action forced -> CONNECT")

        elif force_action == "message":
            action = OutreachAction.MESSAGE
            logger.info("[TEST] Action forced -> MESSAGE")

        else:
            return OutreachResult(
                prospect_id=_DUMMY_ID,
                action=OutreachAction.UNKNOWN,
                success=False,
                error=f"Unknown force_action value: {force_action!r}",
            )

        logger.info("[TEST] Final action: %s", action.value)

        if action == OutreachAction.SKIP:
            logger.info("[TEST] SKIP – connection already pending, no action taken.")
            return OutreachResult(prospect_id=_DUMMY_ID, action=action, success=True)

        if action == OutreachAction.UNKNOWN:
            return OutreachResult(
                prospect_id=_DUMMY_ID,
                action=action,
                success=False,
                error="Could not determine action from page state.",
            )

        try:
            if action == OutreachAction.CONNECT:
                class _ConnectStub:
                    id = _DUMMY_ID
                    outreach_message = message

                note = self._build_connect_note(_ConnectStub())  # type: ignore[arg-type]
                self._open_more_menu_and_click_connect()
                self._settle(800)
                self._handle_connect_modal(note)

            elif action == OutreachAction.MESSAGE:
                if not message:
                    return OutreachResult(
                        prospect_id=_DUMMY_ID,
                        action=action,
                        success=False,
                        error="--message is required when --action=message.",
                    )
                self._open_message_overlay()
                self._type_message(message)
                self._send_message()

        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            logger.error("[TEST] Action failed: %s", err)
            return OutreachResult(
                prospect_id=_DUMMY_ID,
                action=action,
                success=False,
                error=err,
            )

        logger.info("[TEST] Action completed successfully.")
        return OutreachResult(prospect_id=_DUMMY_ID, action=action, success=True)

    # ------------------------------------------------------------------
    # Fetch / persistence layer
    # ------------------------------------------------------------------

    def _fetch_batch(self) -> List[ProspectORM]:
        with session_scope() as session:
            query = (
                session.query(ProspectORM)
                .filter(ProspectORM.outreach_required == True)
                .filter(ProspectORM.outreach_in_progress == False)
                .filter(
                    (ProspectORM.outreach_sent == False)
                    | (
                        (ProspectORM.outreach_status == "FAILED")
                        & (ProspectORM.outreach_attempts < self._max_attempts)
                    )
                )
            )
            if self._agent_id is not None:
                query = query.filter(ProspectORM.agent_id == self._agent_id)

            rows = (
                query
                .order_by(ProspectORM.contact_relevance_score.desc(), ProspectORM.id.asc())
                .limit(self._batch_size)
                .all()
            )
            for r in rows:
                session.expunge(r)
            return rows

    def _lock_and_increment(self, prospect_id: int) -> None:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            row = session.query(ProspectORM).filter_by(id=prospect_id).one()
            row.outreach_in_progress     = True
            row.outreach_attempts        = (row.outreach_attempts or 0) + 1
            row.outreach_last_attempt_ts = now

    def _unlock(self, prospect_id: int) -> None:
        with session_scope() as session:
            row = session.query(ProspectORM).filter_by(id=prospect_id).one()
            row.outreach_in_progress = False

    def _persist_result(self, prospect_id: int, result: OutreachResult) -> None:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            row = session.query(ProspectORM).filter_by(id=prospect_id).one()
            if result.success and result.action not in {OutreachAction.UNKNOWN}:
                if result.action == OutreachAction.SKIP:
                    row.outreach_status = "SKIPPED"
                    row.outreach_error  = None
                else:
                    row.outreach_sent   = True
                    row.outreach_type   = result.action.value
                    row.outreach_status = "SUCCESS"
                    row.outreach_ts     = now
                    row.outreach_error  = None
            else:
                row.outreach_status = "FAILED"
                row.outreach_error  = (result.error or "Unknown error")[:2000]

    # ------------------------------------------------------------------
    # Per-prospect orchestration
    # ------------------------------------------------------------------

    def _process_one(self, prospect: ProspectORM) -> OutreachResult:
        pid = prospect.id
        self._lock_and_increment(pid)
        result = OutreachResult(prospect_id=pid, action=OutreachAction.UNKNOWN, success=False)
        try:
            result = self._execute(prospect)
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            logger.exception("Unhandled error for prospect %d: %s", pid, err_msg)
            result = OutreachResult(
                prospect_id=pid, action=OutreachAction.UNKNOWN,
                success=False, error=err_msg,
            )
        finally:
            self._persist_result(pid, result)
            self._unlock(pid)
        return result

    def _execute(self, prospect: ProspectORM) -> OutreachResult:
        pid = prospect.id
        url = (prospect.linkedin_profile_url or "").strip()
        if not url:
            return OutreachResult(prospect_id=pid, action=OutreachAction.SKIP, success=True)

        logger.info("Outreach prospect %d → %s", pid, url)
        report = self._browser.goto_and_prepare(url)

        if not report.is_usable:
            return OutreachResult(
                prospect_id=pid, action=OutreachAction.UNKNOWN, success=False,
                error=f"Page not usable: {report.state} / {report.reason}",
            )

        self._wait_for_actions_ready()

        conn_state = self._detect_connection_state()
        logger.info(
            "Prospect %d — verdict=%s confidence=%d signals=%s",
            pid, conn_state.verdict, conn_state.confidence, conn_state.signals,
        )

        action = self._decide_action(conn_state, prospect)
        logger.info("Prospect %d — action=%s", pid, action.value)

        if action == OutreachAction.SKIP:
            return OutreachResult(prospect_id=pid, action=action, success=True)

        if action == OutreachAction.UNKNOWN:
            return OutreachResult(
                prospect_id=pid, action=action, success=False,
                error=(
                    f"Cannot determine action. "
                    f"verdict={conn_state.verdict} confidence={conn_state.confidence} "
                    f"signals={conn_state.signals}"
                ),
            )

        if action == OutreachAction.CONNECT:
            self._do_connect(prospect)
        elif action == OutreachAction.MESSAGE:
            self._do_message(prospect)

        return OutreachResult(prospect_id=pid, action=action, success=True)

    # ══════════════════════════════════════════════════════════════════
    # CONNECTION STATE DETECTION
    # ══════════════════════════════════════════════════════════════════

    def _wait_for_actions_ready(self) -> None:
        """
        Wait until the profile action bar is rendered.
        The More button or Follow button are the most reliable landmarks.
        """
        page = self._page()
        combined = f"{_MORE_BTN_SEL}, {_FOLLOW_BTN_SEL}, {_PENDING_BTN_SEL}"
        try:
            page.wait_for_selector(combined, timeout=8000, state="visible")
        except PlaywrightTimeoutError:
            logger.debug("Action bar landmark not detected in 8 s – proceeding.")
        page.wait_for_timeout(3000)

    def _detect_connection_state(self) -> ConnectionState:
        """
        Multi-signal scoring approach. No single selector is trusted alone.

        Signals & weights:
          +20  Degree badge = "1st"               → connected
          +20  Follow button NOT visible           → connected
          +10  Message link with interop present   → connected (supporting)
          -20  Degree badge = "2nd" or "3rd"       → not_connected
          -20  Follow button IS visible             → not_connected
          -10  More-menu contains Connect item      → not_connected (supporting)

        Early-exit (before scoring):
          Pending button visible                   → pending (confidence 95)
          More-menu contains Withdraw item         → pending (confidence 90)

        Verdict thresholds:
          score >= +20  → connected
          score <= -20  → not_connected
          |score| < 20  → ambiguous (lower confidence)
        """
        page = self._page()
        score = 0
        signals: List[str] = []

        # ── Early exit: Pending button directly on page ───────────────
        if self._el_visible(page, _PENDING_BTN_SEL):
            return ConnectionState("pending", 95, ["pending_button_on_page"])

        # ── Signal 1: Degree badge ────────────────────────────────────
        degree = self._read_degree_badge(page)
        if "1st" in degree:
            score += 20
            signals.append("degree_1st")
        elif "2nd" in degree:
            score -= 20
            signals.append("degree_2nd")
        elif "3rd" in degree:
            score -= 20
            signals.append("degree_3rd")

        # ── Signal 2: Follow button presence ─────────────────────────
        if self._el_visible(page, _FOLLOW_BTN_SEL):
            score -= 20
            signals.append("follow_btn_present")
        else:
            score += 20
            signals.append("no_follow_btn")

        # ── Signal 3: Message link with interop (supporting) ─────────
        if self._el_visible(page, _MSG_LINK_SEL):
            score += 10
            signals.append("msg_link_interop_present")

        # ── Signal 4: Scan More menu (supporting + pending check) ─────
        menu_signals = self._scan_more_menu_signals(page)
        if "withdraw" in menu_signals:
            return ConnectionState("pending", 90, signals + ["menu_withdraw"])
        if "connect" in menu_signals:
            score -= 10
            signals.append("menu_has_connect")

        # ── Verdict ───────────────────────────────────────────────────
        if score >= 20:
            return ConnectionState("connected", min(95, 60 + score), signals)
        if score <= -20:
            return ConnectionState("not_connected", min(95, 60 + abs(score)), signals)
        # Ambiguous – lean on the sign
        if score > 0:
            return ConnectionState("connected", 40 + score, signals)
        if score < 0:
            return ConnectionState("not_connected", 40 + abs(score), signals)
        return ConnectionState("unknown", 25, signals)

    def _read_degree_badge(self, page: Page) -> str:
        """
        Read the degree badge text from the profile top-card.
        Real DOM: a <p> tag with text "· 1st", "· 2nd", or "· 3rd".
        """
        for sel in (_DEGREE_1ST, _DEGREE_2ND, _DEGREE_3RD):
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    txt = loc.first.inner_text(timeout=1200).strip()
                    if txt:
                        return txt
            except Exception:
                pass
        return ""

    def _scan_more_menu_signals(self, page: Page) -> List[str]:
        """
        Temporarily open the More menu to detect 'connect' or 'withdraw' items.
        Always closes the menu afterwards.
        Returns a list that may contain "connect" and/or "withdraw".
        """
        found: List[str] = []
        more_btn = self._first_visible(page, [_MORE_BTN_SEL], timeout=2000)
        if more_btn is None:
            return found

        try:
            more_btn.click(timeout=2000)
            page.wait_for_timeout(450)
        except Exception:
            return found

        try:
            if self._el_visible(page, _MENU_WITHDRAW_SEL):
                found.append("withdraw")

            if (
                self._el_visible(page, _MENU_CONNECT_HREF_SEL)
                or self._el_visible(page, _MENU_CONNECT_ARIA_SEL)
                or self._el_visible(page, _MENU_CONNECT_TEXT_SEL)
            ):
                found.append("connect")
        finally:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)
            except Exception:
                pass

        return found

    # ══════════════════════════════════════════════════════════════════
    # DECISION ENGINE
    # ══════════════════════════════════════════════════════════════════

    def _decide_action(
        self, state: ConnectionState, prospect: ProspectORM
    ) -> OutreachAction:
        if state.verdict == "pending":
            logger.info(
                "Prospect %d: connection request already pending → SKIP.", prospect.id
            )
            return OutreachAction.SKIP

        if state.verdict == "connected":
            return OutreachAction.MESSAGE

        if state.verdict == "not_connected":
            return OutreachAction.CONNECT

        logger.warning(
            "Prospect %d: state unknown (confidence=%d, signals=%s) → UNKNOWN.",
            prospect.id, state.confidence, state.signals,
        )
        return OutreachAction.UNKNOWN

    # ══════════════════════════════════════════════════════════════════
    # CONNECT FLOW
    # ══════════════════════════════════════════════════════════════════

    def _do_connect(self, prospect: ProspectORM) -> None:
        """
        Full connect-with-note flow:
          More button → More menu → Connect item → modal →
          (Add a note → textarea → Send invitation)  OR  (Send without a note)
        """
        note = self._build_connect_note(prospect)
        self._open_more_menu_and_click_connect()
        self._settle(800)
        self._handle_connect_modal(note)
        logger.info("Connect request sent for prospect %d.", prospect.id)

    def _open_more_menu_and_click_connect(self) -> None:
        page = self._page()

        # Step 1: Click "More" button
        more_btn = self._first_visible(page, [_MORE_BTN_SEL], timeout=5000)
        if more_btn is None:
            raise RuntimeError(
                "More button (aria-label='More') not found on profile page."
            )
        more_btn.click(timeout=3000)
        self._settle(500)

        # Step 2: Wait for the popover menu
        try:
            page.wait_for_selector(_MORE_MENU_SEL, state="visible", timeout=4000)
        except PlaywrightTimeoutError:
            raise RuntimeError("More-menu popover did not appear after clicking More.")

        # Step 3: Find and click the Connect item
        connect_item = self._first_visible(
            page,
            [
                _MENU_CONNECT_HREF_SEL,   # most reliable: href="/preload/custom-invite/"
                _MENU_CONNECT_ARIA_SEL,   # aria-label="Invite … to connect"
                _MENU_CONNECT_TEXT_SEL,   # text "Connect" inside menu
            ],
            timeout=3000,
        )
        if connect_item is None:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            raise RuntimeError(
                "Connect item not found in More-menu. "
                "Profile may be connected, pending, or the menu changed."
            )

        connect_item.click(timeout=3000)
        self._settle(700)

    def _handle_connect_modal(self, note: Optional[str]) -> None:
        """
        Handle the artdeco modal that appears after clicking Connect.
        The modal contains:
          - "Add a note"       → opens textarea → fill → Send invitation
          - "Send without a note" → sends immediately

        Falls back to _handle_custom_invite_page() if LinkedIn navigates
        to the /preload/custom-invite/ full-page flow instead.
        """
        page = self._page()

        try:
            page.wait_for_selector(_MODAL_ACTIONBAR_SEL, state="visible", timeout=6000)
        except PlaywrightTimeoutError:
            if "/preload/custom-invite/" in (page.url or ""):
                self._handle_custom_invite_page(note)
                return
            raise RuntimeError(
                "Connect modal did not appear and page did not navigate to custom-invite. "
                f"URL: {page.url}"
            )

        if note:
            add_note_btn = self._first_visible(page, [_ADD_NOTE_BTN_SEL], timeout=3000)
            if add_note_btn is None:
                logger.warning(
                    "'Add a note' button not found in modal; falling back to send without note."
                )
                self._click_send_without_note(page)
                return

            add_note_btn.click(timeout=3000)
            self._settle(500)

            textarea = self._first_visible(page, _NOTE_TEXTAREA_SELS, timeout=4000)
            if textarea is None:
                logger.warning(
                    "Note textarea not found after 'Add a note'; falling back to send without note."
                )
                self._click_send_without_note(page)
                return

            textarea.click(timeout=2000)
            textarea.fill(note[:300])   # LinkedIn note hard limit: 300 chars
            self._settle(350)

            send_btn = self._first_visible(page, [_SEND_INVITE_BTN_SEL], timeout=3000)
            if send_btn is None:
                raise RuntimeError(
                    "'Send invitation' button not found after filling note textarea."
                )
            send_btn.click(timeout=4000)
            self._settle(1000)

        else:
            self._click_send_without_note(page)

    def _click_send_without_note(self, page: Page) -> None:
        btn = self._first_visible(page, [_SKIP_NOTE_BTN_SEL], timeout=3000)
        if btn is None:
            raise RuntimeError(
                "'Send without a note' button not found in connect modal."
            )
        btn.click(timeout=4000)
        self._settle(1000)

    def _handle_custom_invite_page(self, note: Optional[str]) -> None:
        """
        Handles the /preload/custom-invite/ page-based flow that LinkedIn
        sometimes uses instead of the modal overlay.
        """
        page = self._page()

        if note:
            textarea = self._first_visible(page, _NOTE_TEXTAREA_SELS, timeout=5000)
            if textarea:
                textarea.click(timeout=2000)
                textarea.fill(note[:300])
                self._settle(350)

        send_btn = self._first_visible(page, [_SEND_INVITE_BTN_SEL], timeout=4000)
        if send_btn is None:
            send_btn = self._first_visible(
                page,
                ["button[type='submit']", "button:has-text('Send')"],
                timeout=3000,
            )
        if send_btn is None:
            raise RuntimeError("Send button not found on custom-invite page.")
        send_btn.click(timeout=4000)
        self._settle(1200)

    # ══════════════════════════════════════════════════════════════════
    # MESSAGE FLOW
    # ══════════════════════════════════════════════════════════════════

    def _do_message(self, prospect: ProspectORM) -> None:
        """
        Full message flow:
          Message link → compose overlay → fill text → send button
        """
        message_text = (prospect.outreach_message or "").strip()
        if not message_text:
            raise RuntimeError(
                f"Prospect {prospect.id} has no outreach_message; cannot send message."
            )
        self._open_message_overlay()
        self._type_message(message_text)
        self._send_message()
        logger.info("Message sent for prospect %d.", prospect.id)

    def _open_message_overlay(self) -> None:
        page = self._page()

        # First dismiss any overlapping popups/banners (Premium upsell etc.)
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        except Exception:
            pass

        # Scroll the action buttons into view cleanly
        try:
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
        except Exception:
            pass

        msg_link = self._first_visible(page, [_MSG_LINK_SEL], timeout=10000)
        if msg_link is None:
            raise RuntimeError(
                "Message link (href contains /messaging/compose/ + interop=msgOverlay) "
                "not found on profile page."
            )

        # Use JS click to bypass any overlapping elements
        try:
            msg_link.evaluate("el => el.click()")
        except Exception:
            msg_link.click(timeout=8000)

        self._settle(800)

        compose_sel = ", ".join(_MSG_COMPOSE_SELS)
        try:
            page.wait_for_selector(compose_sel, state="visible", timeout=9000)
        except PlaywrightTimeoutError:
            raise RuntimeError(
                "Message compose area did not appear after clicking Message link."
            )

    def _type_message(self, text: str) -> None:
        page = self._page()
        compose = self._first_visible(page, _MSG_COMPOSE_SELS, timeout=4000)
        if compose is None:
            raise RuntimeError("Message compose area not found when attempting to type.")
        compose.click(timeout=2000)
        self._settle(200)
        compose.fill(text)
        self._settle(300)

    def _send_message(self) -> None:
        page = self._page()
        send_btn = self._first_visible(page, _MSG_SEND_BTN_SELS, timeout=4000)
        if send_btn is None:
            raise RuntimeError(
                "Message send button (msg-form__send-button[type='submit']) not found."
            )
        send_btn.click(timeout=4000)
        self._settle(900)

    # ══════════════════════════════════════════════════════════════════
    # UI UTILITY HELPERS
    # ══════════════════════════════════════════════════════════════════

    def _page(self) -> Page:
        page = self._browser.page
        if page is None:
            raise RuntimeError("LinkedInBrowser not started – call start() first.")
        return page

    def _el_visible(self, page: Page, selector: str, *, timeout: int = 900) -> bool:
        """Return True if at least one element matching selector is visible."""
        try:
            loc = page.locator(selector)
            if loc.count() == 0:
                return False
            return loc.first.is_visible(timeout=timeout)
        except Exception:
            return False

    def _first_visible(
        self,
        page: Page,
        selectors: List[str],
        *,
        timeout: int = 2000,
    ) -> Optional[Locator]:
        """
        Return the first visible Locator from a priority-ordered selector list.
        Iterates each selector in order; within each selector tries up to 3 matches.
        Returns None if nothing is found.
        """
        for sel in selectors:
            try:
                loc = page.locator(sel)
                count = loc.count()
                for idx in range(min(count, 3)):
                    item = loc.nth(idx)
                    try:
                        if item.is_visible(timeout=timeout):
                            return item
                    except Exception:
                        continue
            except Exception:
                continue
        return None

    def _settle(self, ms: int = 500) -> None:
        """Short wait for async UI updates to settle."""
        try:
            self._page().wait_for_timeout(ms)
        except Exception:
            time.sleep(ms / 1000)

    # ══════════════════════════════════════════════════════════════════
    # HUMANISATION
    # ══════════════════════════════════════════════════════════════════

    def _human_delay(self) -> None:
        delay = random.uniform(self._delay_min, self._delay_max)
        logger.debug("Human delay: %.1fs between prospects.", delay)
        time.sleep(delay)

    # ══════════════════════════════════════════════════════════════════
    # NOTE BUILDER
    # ══════════════════════════════════════════════════════════════════

    def _build_connect_note(self, prospect: ProspectORM) -> Optional[str]:
        """
        Build the connection note.
        Priority:
          1. Pre-generated outreach_message (if ≤ 300 chars, use as-is)
          2. Truncated outreach_message (at sentence boundary)
          3. Static connect_note from config
          4. None → "Send without a note"
        """
        if prospect.outreach_message:
            msg = prospect.outreach_message.strip()
            if len(msg) <= 300:
                return msg
            truncated = msg[:297]
            last_period = truncated.rfind(". ")
            if last_period > 100:
                return truncated[: last_period + 1]
            return truncated + "..."

        return self._connect_note or None
