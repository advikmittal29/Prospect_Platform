"""
LinkedIn Reply Handler
======================
Polls the LinkedIn inbox for replies from prospects who have already received
our first outreach message, generates an AI reply via Gemini, and sends it
back — all automatically.

Flow per polling cycle
----------------------
1. Load all active conversations (outreach_sent=True, conversation_status='active')
2. For each prospect → open their LinkedIn message thread via Playwright
3. Scrape the full thread from the overlay
4. Compare with stored thread_json — detect any NEW messages from them
5. If new message found:
      a. Build full thread context
      b. Call Gemini to generate reply
      c. Send reply via the existing _type_message / _send_message helpers
      d. Update linkedin_conversations row
6. Update last_checked_utc regardless

Designed to slot directly into the ProspectOS architecture:
  - Uses LinkedInBrowser (same as outreach sender)
  - Uses session_scope / ORM (same DB layer)
  - Follows the same logger pattern
  - Entry point: scheduler/p_reply_handler.py  (mirrors p_outreach.py)
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import google.generativeai as genai
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from config import AppConfig
from db import ProspectORM, session_scope
from db.models import LinkedInConversationORM
from research.linkedin_browser import LinkedInBrowser
from utils.logging import build_logger

logger = build_logger("prospect.outreach.reply_handler")


# ---------------------------------------------------------------------------
# LinkedIn DOM selectors for the messaging overlay
# ---------------------------------------------------------------------------

# Message link on the profile page (same as outreach sender)
_MSG_LINK_SEL = "a[href*='/messaging/compose/'][href*='interop=msgOverlay']"

# The compose overlay container
_MSG_OVERLAY_SEL = "div.msg-overlay-conversation-bubble"

# Each message bubble inside the overlay
# Real DOM: <div class="msg-s-message-list__event"> contains sender + text
_MSG_BUBBLE_SEL  = "div.msg-s-event-listitem"
_MSG_SENDER_SEL  = ".msg-s-message-group__profile-link"   # sender name inside bubble group
_MSG_TEXT_SEL    = ".msg-s-event-listitem__body"           # message text

# Compose box (reuse from sender)
_MSG_COMPOSE_SELS = [
    "div.msg-form__contenteditable[role='textbox']",
    "div[role='textbox'][aria-label='Write a message\u2026']",
    "div[contenteditable='true'][aria-label='Write a message\u2026']",
    "div.msg-form__contenteditable",
]

# Send button (reuse from sender)
_MSG_SEND_BTN_SELS = [
    "button.msg-form__send-button[type='submit']",
    "button.msg-form__send-button",
]

# Delay between prospects during a poll cycle
_INTER_PROSPECT_DELAY_MIN = 15   # seconds
_INTER_PROSPECT_DELAY_MAX = 35


# ---------------------------------------------------------------------------
# Gemini reply generator
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """
You are a senior B2B recruitment consultant representing a staffing agency.
You are having a LinkedIn conversation with a hiring manager or senior leader
at a company that has open job postings.

Your goal: Build rapport, understand their hiring needs, and move toward a
call or meeting to discuss how your agency can help them fill roles faster.

Rules:
- Be professional, warm, and concise. LinkedIn messages should be SHORT (3-6 sentences max).
- Never be pushy or salesy. Mirror their energy and tone.
- If they seem interested → gently suggest a call.
- If they ask a question → answer it directly and turn it back to their needs.
- If they seem uninterested or say they have internal HR → acknowledge gracefully,
  leave the door open, and close the conversation politely.
- Never repeat phrases from previous messages verbatim.
- Always respond in plain text. No bullet points, no markdown, no emojis.
- Sign off with your first name only (e.g. "- Alex").
""".strip()


class GeminiReplyGenerator:
    """
    Wraps the Gemini API to generate a contextual LinkedIn reply
    given the full conversation thread.
    """

    def __init__(self, api_key: str, model_name: str = "gemini-1.5-flash") -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=_SYSTEM_PROMPT,
        )

    def generate_reply(
        self,
        thread: list[dict],
        prospect_name: str,
        prospect_title: str,
        company_name: str,
        recruiter_name: str,
        agency_name: str,
        agency_pitch: str,
    ) -> str:
        """
        Generate the next reply message.

        Parameters
        ----------
        thread        : list of {"role": "us"|"them", "text": "...", "ts": "..."}
        prospect_name : display name of the hiring manager
        ...

        Returns
        -------
        Plain-text reply string.
        """
        # Build thread context for the prompt
        thread_lines = []
        for msg in thread:
            role_label = recruiter_name if msg["role"] == "us" else prospect_name
            thread_lines.append(f"[{role_label}]: {msg['text']}")

        thread_str = "\n\n".join(thread_lines)

        prompt = f"""
You are {recruiter_name} from {agency_name}.

About {agency_name}: {agency_pitch}

You are messaging {prospect_name} ({prospect_title}) at {company_name} on LinkedIn.

Here is the conversation so far:

{thread_str}

Write your next reply to {prospect_name}. Remember: short, warm, professional.
Do not start with "Hi {prospect_name}" every time — vary your openers.
""".strip()

        try:
            response = self._model.generate_content(prompt)
            reply = (response.text or "").strip()
            if not reply:
                raise ValueError("Gemini returned empty response.")
            return reply
        except Exception as exc:
            logger.error("Gemini generation failed: %s", exc)
            raise


# ---------------------------------------------------------------------------
# Thread scraper
# ---------------------------------------------------------------------------

class LinkedInThreadScraper:
    """
    Opens the LinkedIn message overlay for a given profile and scrapes
    the full visible thread.

    Returns a list of dicts: {"role": "us"|"them", "text": "..."}
    The caller is responsible for comparing with stored thread to find new messages.
    """

    def __init__(self, browser: LinkedInBrowser, my_name: str) -> None:
        self._browser  = browser
        self._my_name  = my_name.strip().lower()   # used to classify "us" vs "them"

    def scrape_thread(self, profile_url: str) -> List[dict]:
        """
        Navigate to the prospect's LinkedIn profile, click Message,
        and scrape the full conversation thread from the overlay.

        Returns [] if the overlay could not be opened or no messages found.
        """
        page = self._browser.page
        if page is None:
            raise RuntimeError("Browser not started.")

        # Navigate to the profile
        logger.debug("Navigating to profile: %s", profile_url)
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
        except Exception as exc:
            logger.warning("Failed to navigate to %s: %s", profile_url, exc)
            return []

        # Click Message link
        msg_link = self._first_visible(page, [_MSG_LINK_SEL], timeout=6000)
        if msg_link is None:
            logger.warning("Message link not found on %s", profile_url)
            return []

        try:
            msg_link.click(timeout=4000)
            page.wait_for_timeout(1500)
        except Exception as exc:
            logger.warning("Could not click Message link: %s", exc)
            return []

        # Wait for overlay
        try:
            page.wait_for_selector(_MSG_OVERLAY_SEL, state="visible", timeout=8000)
        except PlaywrightTimeoutError:
            logger.warning("Message overlay did not appear for %s", profile_url)
            return []

        # Give messages time to render
        page.wait_for_timeout(1200)

        # Scroll to top of overlay to load older messages
        try:
            overlay = page.locator(_MSG_OVERLAY_SEL).first
            page.evaluate(
                "(el) => { el.scrollTop = 0; }",
                overlay.element_handle(),
            )
            page.wait_for_timeout(800)
        except Exception:
            pass

        # Scrape message bubbles
        thread = self._extract_bubbles(page)

        # Close the overlay (press Escape) so it doesn't interfere with next profile
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception:
            pass

        return thread

    def _extract_bubbles(self, page: Page) -> List[dict]:
        """
        Extract all visible message bubbles from the overlay.
        Returns list of {"role": "us"|"them", "text": "..."} in chronological order.
        """
        thread = []
        try:
            # Each listitem represents one message event
            items = page.locator(_MSG_BUBBLE_SEL).all()
            for item in items:
                try:
                    text_el = item.locator(_MSG_TEXT_SEL)
                    if text_el.count() == 0:
                        continue
                    text = text_el.first.inner_text(timeout=1000).strip()
                    if not text:
                        continue

                    # Determine sender: look for aria-label or profile link
                    # LinkedIn groups messages; the sender label appears at the group top
                    # We use a broader parent search for the group header
                    role = self._classify_sender(item, page)
                    thread.append({"role": role, "text": text})
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("Error extracting message bubbles: %s", exc)

        return thread

    def _classify_sender(self, item, page: Page) -> str:
        """
        Try to classify whether this message was sent by us or by them.
        LinkedIn DOM signals:
          - Our messages: have class 'msg-s-message-list__event--right' or
            the bubble has data-test-msg-body-author containing our name
          - Their messages: no 'right' class, or author != our name
        """
        try:
            # Check for 'right' alignment class (our messages are right-aligned)
            classes = item.get_attribute("class") or ""
            if "right" in classes.lower():
                return "us"

            # Fallback: check aria-label on the listitem
            aria = item.get_attribute("aria-label") or ""
            if self._my_name and self._my_name in aria.lower():
                return "us"

            return "them"
        except Exception:
            return "them"  # safe default — treat unknown as their message

    def _first_visible(self, page: Page, selectors: list, *, timeout: int = 2000):
        for sel in selectors:
            try:
                loc = page.locator(sel)
                count = loc.count()
                for idx in range(min(count, 3)):
                    item = loc.nth(idx)
                    if item.is_visible(timeout=timeout):
                        return item
            except Exception:
                continue
        return None


# ---------------------------------------------------------------------------
# Reply sender (thin wrapper reusing outreach sender internals)
# ---------------------------------------------------------------------------

class LinkedInReplySender:
    """
    Sends a message into an already-open Message overlay.
    Reuses the same DOM selectors as LinkedInOutreachSender._type_message / _send_message.
    """

    def __init__(self, browser: LinkedInBrowser) -> None:
        self._browser = browser

    def send(self, text: str) -> None:
        page = self._browser.page
        if page is None:
            raise RuntimeError("Browser not started.")

        # Find compose area
        compose = None
        for sel in _MSG_COMPOSE_SELS:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible(timeout=1500):
                    compose = loc.first
                    break
            except Exception:
                continue

        if compose is None:
            raise RuntimeError("Message compose area not found when trying to send reply.")

        compose.click(timeout=2000)
        page.wait_for_timeout(200)
        compose.fill(text)
        page.wait_for_timeout(350)

        # Find send button
        send_btn = None
        for sel in _MSG_SEND_BTN_SELS:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible(timeout=1500):
                    send_btn = loc.first
                    break
            except Exception:
                continue

        if send_btn is None:
            raise RuntimeError("Send button not found when trying to send reply.")

        send_btn.click(timeout=4000)
        page.wait_for_timeout(900)
        logger.info("Reply sent successfully.")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class LinkedInReplyHandler:
    """
    Orchestrates one full polling cycle:
      Load active conversations → scrape each thread → detect new replies
      → generate AI reply → send → update DB.
    """

    def __init__(
        self,
        browser: LinkedInBrowser,
        config: AppConfig,
        agent_id: Optional[int] = None,
    ) -> None:
        self._browser   = browser
        self._cfg       = config
        self._agent_id  = agent_id

        # Gemini setup — reads GOOGLE_API_KEY from config
        gemini_api_key  = config.llm.api_key          # same key used by dossier generator
        self._gemini    = GeminiReplyGenerator(api_key=gemini_api_key)

        # Your LinkedIn display name (used to classify "us" vs "them" in threads)
        # Set MY_LINKEDIN_NAME in .env  e.g.  MY_LINKEDIN_NAME="Advik Sharma"
        self._my_name       = getattr(config, "my_linkedin_name", "") or ""
        self._recruiter_name = config.outreach.recruiter_name or "Alex"
        self._agency_name   = config.outreach.agency_name    or "RecruitPro"
        self._agency_pitch  = getattr(config.outreach, "agency_pitch", "") or (
            "We specialise in placing top engineering and tech talent for "
            "high-growth companies across India. We handle sourcing, screening, "
            "and shortlisting — so your team interviews only pre-qualified candidates."
        )

        self._scraper = LinkedInThreadScraper(browser, self._my_name)
        self._sender  = LinkedInReplySender(browser)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        One polling cycle.  Returns stats dict.
        """
        stats = {
            "checked":        0,
            "new_replies":    0,
            "ai_replies_sent": 0,
            "errors":         0,
            "skipped":        0,
        }

        conversations = self._load_active_conversations()

        if not conversations:
            logger.info("ReplyHandler: no active conversations to check.")
            return stats

        logger.info("ReplyHandler: checking %d active conversation(s).", len(conversations))

        for conv in conversations:
            stats["checked"] += 1
            try:
                result = self._process_one(conv)
                if result == "replied":
                    stats["new_replies"]     += 1
                    stats["ai_replies_sent"] += 1
                elif result == "no_new":
                    pass
                elif result == "skipped":
                    stats["skipped"] += 1
            except Exception as exc:
                logger.exception(
                    "Unhandled error processing conversation for prospect %d: %s",
                    conv.prospect_id, exc,
                )
                stats["errors"] += 1
                self._record_error(conv.id, str(exc))

            # Human-like delay between checks
            delay = random.uniform(_INTER_PROSPECT_DELAY_MIN, _INTER_PROSPECT_DELAY_MAX)
            logger.debug("Waiting %.1fs before next prospect…", delay)
            time.sleep(delay)

        logger.info("ReplyHandler cycle complete. Stats: %s", stats)
        return stats

    # ------------------------------------------------------------------
    # Single conversation processor
    # ------------------------------------------------------------------

    def _process_one(self, conv: LinkedInConversationORM) -> str:
        """
        Returns "replied" | "no_new" | "skipped"
        """
        prospect = self._load_prospect(conv.prospect_id)
        if prospect is None:
            logger.warning("Prospect %d not found in DB — skipping.", conv.prospect_id)
            return "skipped"

        profile_url = conv.linkedin_profile_url
        logger.info(
            "Checking conversation for prospect %d (%s)",
            conv.prospect_id, prospect.name or profile_url,
        )

        # 1. Scrape current thread from LinkedIn
        live_thread = self._scraper.scrape_thread(profile_url)
        self._update_last_checked(conv.id)

        if not live_thread:
            logger.debug("No messages found in overlay for prospect %d.", conv.prospect_id)
            return "no_new"

        # 2. Compare with stored thread to find new messages from them
        stored_thread = conv.get_thread()
        new_their_messages = self._find_new_their_messages(stored_thread, live_thread)

        if not new_their_messages:
            logger.debug("No new replies from prospect %d.", conv.prospect_id)
            return "no_new"

        logger.info(
            "New reply(ies) from prospect %d (%s): %d message(s)",
            conv.prospect_id,
            prospect.name or "unknown",
            len(new_their_messages),
        )

        # 3. Build updated thread (stored + any new messages)
        updated_thread = stored_thread.copy()
        for msg in new_their_messages:
            updated_thread.append(msg)

        # 4. Generate AI reply
        reply_text = self._gemini.generate_reply(
            thread         = updated_thread,
            prospect_name  = prospect.name or "there",
            prospect_title = prospect.current_title or prospect.headline or "Hiring Manager",
            company_name   = self._get_company_name(prospect.company_research_id),
            recruiter_name = self._recruiter_name,
            agency_name    = self._agency_name,
            agency_pitch   = self._agency_pitch,
        )

        logger.info("Generated reply (%d chars): %s…", len(reply_text), reply_text[:80])

        # 5. Re-open the conversation overlay and send the reply
        #    (scrape_thread already closed it with Escape — reopen via profile)
        self._reopen_overlay(profile_url)
        self._sender.send(reply_text)

        # 6. Append reply to thread and persist to DB
        updated_thread.append({
            "role": "us",
            "text": reply_text,
            "ts":   datetime.now(timezone.utc).isoformat(),
        })
        self._persist_reply(conv, updated_thread, new_their_messages)

        return "replied"

    # ------------------------------------------------------------------
    # Thread diffing
    # ------------------------------------------------------------------

    def _find_new_their_messages(
        self,
        stored: list[dict],
        live:   list[dict],
    ) -> list[dict]:
        """
        Return messages in `live` that are from 'them' and not yet in `stored`.

        Strategy: count how many 'them' messages are in stored, then return
        any extras from live that come after that count.

        This is intentionally simple and robust — it doesn't do text matching
        (which breaks if LinkedIn truncates or reformats messages).
        """
        stored_their_count = sum(1 for m in stored if m.get("role") == "them")
        live_their = [m for m in live if m.get("role") == "them"]

        if len(live_their) > stored_their_count:
            new_msgs = live_their[stored_their_count:]
            # Stamp timestamps since live scrape doesn't have them
            now_iso = datetime.now(timezone.utc).isoformat()
            for m in new_msgs:
                m.setdefault("ts", now_iso)
            return new_msgs

        return []

    # ------------------------------------------------------------------
    # Re-open the message overlay to send a reply
    # ------------------------------------------------------------------

    def _reopen_overlay(self, profile_url: str) -> None:
        """
        Navigate to the profile and click Message to re-open the overlay.
        The scraper closes it with Escape after reading, so we reopen here.
        """
        page = self._browser.page
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
        except Exception as exc:
            raise RuntimeError(f"Could not navigate to {profile_url}: {exc}") from exc

        msg_link = None
        for sel in [_MSG_LINK_SEL]:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible(timeout=5000):
                    msg_link = loc.first
                    break
            except Exception:
                pass

        if msg_link is None:
            raise RuntimeError(f"Message link not found on {profile_url} when trying to reply.")

        msg_link.click(timeout=4000)
        page.wait_for_timeout(1500)

        # Wait for compose area to be ready
        compose_sel = ", ".join(_MSG_COMPOSE_SELS)
        try:
            page.wait_for_selector(compose_sel, state="visible", timeout=8000)
        except PlaywrightTimeoutError:
            raise RuntimeError("Compose area did not appear after clicking Message for reply.")

    # ------------------------------------------------------------------
    # DB operations
    # ------------------------------------------------------------------

    def _load_active_conversations(self) -> List[LinkedInConversationORM]:
        """
        Load all conversations that are active and belong to prospects
        who have had their first message sent (outreach_sent=True).
        """
        with session_scope() as session:
            rows = (
                session.query(LinkedInConversationORM)
                .filter(
                    LinkedInConversationORM.conversation_status == "active",
                )
                .order_by(LinkedInConversationORM.last_checked_utc.asc().nullsfirst())
                .all()
            )
            # Apply agent filter if provided
            if self._agent_id is not None:
                rows = [r for r in rows if r.agent_id == self._agent_id]
            for r in rows:
                session.expunge(r)
            return rows

    def _load_prospect(self, prospect_id: int) -> Optional[ProspectORM]:
        with session_scope() as session:
            row = session.query(ProspectORM).filter_by(id=prospect_id).one_or_none()
            if row:
                session.expunge(row)
            return row

    def _get_company_name(self, company_research_id: int) -> str:
        try:
            from db import CompanyResearchORM
            with session_scope() as session:
                row = session.query(CompanyResearchORM).filter_by(
                    id=company_research_id
                ).one_or_none()
                if row:
                    return row.company_name or "their company"
        except Exception:
            pass
        return "their company"

    def _persist_reply(
        self,
        conv:           LinkedInConversationORM,
        updated_thread: list[dict],
        new_their_msgs: list[dict],
    ) -> None:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            row = session.query(LinkedInConversationORM).filter_by(id=conv.id).one()
            row.thread_json             = json.dumps(updated_thread)
            row.messages_received       = (row.messages_received or 0) + len(new_their_msgs)
            row.messages_sent           = (row.messages_sent or 0) + 1
            row.last_reply_received_utc = now
            row.last_message_sent_utc   = now
            row.last_checked_utc        = now
            row.last_error              = None  # clear previous errors on success

            # Basic lead stage upgrade logic
            total_their = row.messages_received
            if total_their >= 1 and row.lead_stage == "cold":
                row.lead_stage = "warming"
            if total_their >= 2 and row.lead_stage == "warming":
                row.lead_stage = "interested"

    def _update_last_checked(self, conv_id: int) -> None:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            row = session.query(LinkedInConversationORM).filter_by(id=conv_id).one_or_none()
            if row:
                row.last_checked_utc = now

    def _record_error(self, conv_id: int, error: str) -> None:
        with session_scope() as session:
            row = session.query(LinkedInConversationORM).filter_by(id=conv_id).one_or_none()
            if row:
                row.last_error  = error[:2000]
                row.error_count = (row.error_count or 0) + 1

    # ------------------------------------------------------------------
    # Utility: create a conversation row when first message is sent
    # ------------------------------------------------------------------

    @staticmethod
    def create_conversation_for_prospect(
        prospect_id:          int,
        agent_id:             Optional[int],
        linkedin_profile_url: str,
        first_message_text:   str,
    ) -> None:
        """
        Call this AFTER successfully sending the first outreach message.
        Creates the linkedin_conversations row so the poller can start tracking.

        Idempotent — does nothing if a row already exists for this prospect.
        """
        now = datetime.now(timezone.utc)
        initial_thread = [
            {
                "role": "us",
                "text": first_message_text,
                "ts":   now.isoformat(),
            }
        ]
        with session_scope() as session:
            existing = session.query(LinkedInConversationORM).filter_by(
                prospect_id=prospect_id
            ).one_or_none()

            if existing:
                logger.debug(
                    "Conversation row already exists for prospect %d — skipping create.",
                    prospect_id,
                )
                return

            conv = LinkedInConversationORM(
                prospect_id          = prospect_id,
                agent_id             = agent_id,
                linkedin_profile_url = linkedin_profile_url,
                conversation_status  = "active",
                lead_stage           = "cold",
                thread_json          = json.dumps(initial_thread),
                messages_sent        = 1,
                messages_received    = 0,
                first_message_sent_utc = now,
                last_message_sent_utc  = now,
                last_checked_utc       = None,
            )
            session.add(conv)
            logger.info(
                "Created conversation row for prospect %d (%s).",
                prospect_id, linkedin_profile_url,
            )