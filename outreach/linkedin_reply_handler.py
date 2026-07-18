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
      a. Classify the message (LIGHT_QUESTION / INTERESTED / NOT_INTERESTED_NEUTRAL)
      b. If it crosses the interest/cap thresholds → hand off to a human (silent,
         + one manager email) and stop touching this conversation.
      c. Otherwise build full thread context, call Gemini, and send the reply with
         humanized (character-by-character) typing, timed live-vs-batch.
6. Update last_checked_utc regardless
7. Any conversation detected as "live" gets a bounded fast-follow re-poll before
   the run returns, so an actively-engaged prospect isn't left waiting ~10 min.

Designed to slot directly into the ProspectOS architecture:
  - Uses LinkedInBrowser (same as outreach sender)
  - Uses session_scope / ORM (same DB layer)
  - Follows the same logger pattern
  - Entry point: scheduler/p_reply_handler.py  (mirrors p_outreach.py)

CHANGELOG (this revision)
--------------------------
- Added humanized conversational behavior: centralized voice/persona rules
  (prompts/reply_voice_rules.txt), an LLM-backed reply classifier with a keyword
  safety net, manager handoff (silent + email) on real interest or reply caps,
  human-paced character-by-character typing, and live-vs-batch reply timing with
  a bounded fast-follow loop. See CLAUDE.md for the full design notes.
- (Prior revision) Added promo/upsell redirect detection (_is_promo_page /
  _recover_from_promo). LinkedIn sometimes intercepts a click near the Message
  button with a Premium / Sales Navigator upsell overlay and the click lands on
  a promo page instead of opening the thread. Each of the 3 existing
  open-attempts (normal click, force click, direct href navigation) now checks
  for this immediately after firing, and if detected, marks that attempt as
  failed and recovers back to the profile page before the next attempt runs.
"""
from __future__ import annotations

import html
import json
import queue
import random
import re
import smtplib
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import List, Optional, Tuple

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from sqlalchemy import func

from config import AppConfig, ReplyPolicyConfig
from utils.llm_client import llm_complete
from utils.prompt_loader import render_prompt
from db import ProspectORM, session_scope
from db.models import LinkedInConversationORM
from research.linkedin_browser import LinkedInBrowser
from utils.logging import build_logger
from rag.retriever import Retriever

logger = build_logger("prospect.outreach.reply_handler")


# ---------------------------------------------------------------------------
# LinkedIn DOM selectors for the messaging overlay
# ---------------------------------------------------------------------------

# Message link on the profile page.
# LinkedIn uses a compose URL for first contact and a thread URL once a thread exists.
# We must match BOTH — this is the primary reason scraping fails on existing conversations.
_MSG_LINK_SELS = [
    "a[href*='/messaging/compose/'][href*='interop=msgOverlay']",  # first contact
    "a[href*='/messaging/thread/']",                                # existing thread
    "a[href*='/messaging/compose/']",                               # compose without overlay param
]

# The compose overlay container (present when thread is opened from a profile page)
_MSG_OVERLAY_SEL = "div.msg-overlay-conversation-bubble"

# Selectors for the full LinkedIn messaging page (present when navigating to thread URL directly)
_MSG_FULLPAGE_SELS = [
    "div.msg-s-message-list",
    "div.scaffold-layout__main",
]

# Each message bubble inside the overlay
# Real DOM: <div class="msg-s-message-list__event"> contains sender + text
_MSG_BUBBLE_SEL  = "div.msg-s-event-listitem"
_MSG_TEXT_SEL    = ".msg-s-event-listitem__body"           # message text

# Compose box (reuse from sender)
_MSG_COMPOSE_SELS = [
    "div.msg-form__contenteditable[role='textbox']",
    "div[role='textbox'][aria-label='Write a message…']",
    "div[contenteditable='true'][aria-label='Write a message…']",
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

# URL fragments indicating LinkedIn redirected us to a promo/upsell page
# (Premium, Sales Navigator, Learning, checkout, etc.) instead of the
# profile/messaging page we expected. Clicking near the action buttons
# sometimes triggers these via an intercepting overlay element.
_PROMO_URL_MARKERS = ("/premium/", "/sales/", "/redeem/", "/checkout/", "/learning/", "upsellOrderOrigin")

# Keyword fast-path safety net: if the prospect's message contains any of these,
# force INTERESTED regardless of what the LLM classifier says. Cheap, deterministic,
# and catches the highest-stakes case (pricing / call requests) even if the LLM call
# fails or misclassifies.
_INTERESTED_KEYWORDS = (
    "price", "pricing", "cost", "budget", "how much", "charge", "rate",
    "call", "meeting", "meet up", "schedule a call", "hop on a call",
    "talk further", "chat further", "discuss further", "tell me more",
    "let's talk", "lets talk", "demo", "quote", "proposal",
    # Explicit declarations of interest — the highest-stakes phrases there are,
    # so they must not depend on the LLM being reachable. Spelled out with the
    # pronoun ("i'm interested", not bare "interested") so they cannot match
    # inside "not interested" / "i'm not interested".
    "i'm interested", "im interested", "i am interested",
    "we're interested", "were interested", "we are interested",
    "sounds interesting", "looks promising",
)

# Hard-decline fast-path: unambiguous rejections close the conversation as
# not_interested without burning LLM calls. Kept deliberately conservative —
# anything softer falls through to the LLM classifier.
_NOT_INTERESTED_KEYWORDS = (
    "not interested", "no thanks", "no thank you", "please stop", "stop messaging",
    "do not contact", "don't contact", "dont contact", "unsubscribe", "remove me",
)

_CLASSIFY_LABELS = {"LIGHT_QUESTION", "INTERESTED", "NOT_INTERESTED", "NOT_INTERESTED_NEUTRAL"}


def _compile_keyword_re(keywords: Tuple[str, ...]) -> "re.Pattern":
    """
    Match keywords only as whole words.

    Plain substring matching fires on innocent text: "rate" hits inside
    "corporate"/"accelerate", "call" inside "specifically"/"technically", "demo"
    inside "democratic". Every one of those would be read as a buying signal and
    email the manager a false lead. (?<!\\w)/(?!\\w) rather than \\b so keywords
    containing an apostrophe — "i'm interested", "let's talk" — still anchor.
    """
    alternatives = "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


_INTERESTED_RE = _compile_keyword_re(_INTERESTED_KEYWORDS)
_NOT_INTERESTED_RE = _compile_keyword_re(_NOT_INTERESTED_KEYWORDS)

# Serialises the actual send step across parallel worker threads so the
# account-wide minimum send spacing stays honest even with N tabs scraping
# concurrently. Scraping, classification, and LLM generation stay parallel.
_GLOBAL_SEND_LOCK = threading.Lock()


def _prospect_appears_online(page: Page) -> bool:
    """
    Best-effort check for the prospect's green presence dot on the currently
    open thread/profile. Returns False on any doubt — presence is only a HINT
    that buys a longer linger window; a reply is the only signal that starts a
    live session. Selector set is unverified against a live LinkedIn DOM
    snapshot (same caveat as every selector in this project): if LinkedIn's
    markup doesn't expose presence, this simply returns False and the shorter
    offline grace window applies.
    """
    try:
        return bool(page.evaluate(
            """() => {
                // 1. Semantic presence indicator classes (LinkedIn presence widget)
                const nodes = document.querySelectorAll(
                    '[class*="presence-entity__indicator"], [class*="presence-indicator"]'
                );
                for (const n of nodes) {
                    const cls = (n.className || '').toString().toLowerCase();
                    if (cls.includes('online') || cls.includes('is-reachable')) return true;
                }
                // 2. Accessibility text LinkedIn attaches to presence dots
                const hidden = document.querySelectorAll('.visually-hidden, [aria-label]');
                for (const n of hidden) {
                    const t = (((n.getAttribute && n.getAttribute('aria-label')) || '')
                               + ' ' + (n.textContent || '')).toLowerCase();
                    if (t.includes('is online') || t.includes('active now')
                        || t.includes('status is online') || t.includes('status is reachable')) return true;
                }
                return false;
            }"""
        ))
    except Exception:
        return False


def _is_promo_page(page: Page) -> bool:
    """Return True if the current page URL looks like a promo/upsell redirect."""
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    return any(marker.lower() in url for marker in _PROMO_URL_MARKERS)


def _recover_from_promo(page: Page, target_url: str, log) -> None:
    """
    If we've been redirected to a promo page, dismiss it and navigate
    back to the intended target (profile) page before the next attempt.
    """
    log.warning("[GUARD] Redirected to promo page (%s) — recovering to %s", page.url, target_url)
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass
    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)
    except Exception as exc:
        log.warning("[GUARD] Recovery navigation failed: %s", exc)


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Best-effort ISO8601 parse; returns None (never raises) on anything unparsable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _normalize_msg_text(text: str) -> str:
    """Collapse whitespace and case so a scraped bubble matches what we typed."""
    return " ".join((text or "").split()).strip().lower()


def _fingerprint_msg_text(text: str) -> str:
    """
    Aggressive fingerprint for matching a scraped bubble to what we sent: keep
    only lowercase alphanumerics. This survives the rendering drift that plain
    normalization does not — emoji, punctuation changes, collapsed whitespace,
    link expansion — so an echo of our own message is still recognised as ours.
    """
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# Below this many fingerprint chars a prefix/truncation match is too risky
# (short generic bubbles like "ok thanks" could collide), so only exact matches
# count. Full messages easily clear it.
_ECHO_PREFIX_MIN = 20


def _our_fingerprints(thread: List[dict]) -> List[str]:
    """Fingerprints of every message we have sent in this thread."""
    return [
        _fingerprint_msg_text(m.get("text"))
        for m in thread
        if m.get("role") == "us" and (m.get("text") or "").strip()
    ]


# Back-compat alias — some call sites build the "ours" collection separately.
def _our_text_set(thread: List[dict]) -> List[str]:
    return _our_fingerprints(thread)


def _is_our_message(scraped: str, our_fps) -> bool:
    """
    True if a scraped bubble is (robustly) one of our own sent messages.

    Matches on exact fingerprint, or a substantial shared prefix in either
    direction — LinkedIn truncates long bubbles with "See more" (scraped text is
    a prefix of ours) and sometimes appends chrome (ours is a prefix of scraped).
    The 20-char floor keeps a distinct prospect reply from ever colliding with
    the start of one of our messages.
    """
    fp = _fingerprint_msg_text(scraped)
    if not fp:
        return False
    n = _ECHO_PREFIX_MIN
    for ours in our_fps:
        if not ours:
            continue
        if fp == ours:
            return True
        # Truncation ("See more") or appended chrome diverges from our text at the
        # cut point, so a full prefix check fails — but a long shared LEADING run
        # is conclusive. A distinct prospect reply won't share the first n
        # alphanumerics of one of our messages.
        if len(fp) >= n and len(ours) >= n and fp[:n] == ours[:n]:
            return True
    return False


def _relabel_our_own_bubbles(bubbles: List[dict], our_fps) -> int:
    """
    Re-label any scraped bubble that is really one of our sent messages as "us".
    Returns the number of bubbles corrected.

    _classify_sender reads the DOM to decide who sent a bubble, but LinkedIn
    exposes no dependable marker on this surface (hashed class names, no aria
    labels), so it falls through to its "them" default for our OWN outbound.
    That default turns every message we send into a fresh inbound: the live
    session then answers itself, and each answer becomes the next "inbound",
    looping until a cap trips.

    We know exactly what we sent, so text identity is a far stronger signal than
    anything the DOM offers — this runs on top of _classify_sender and corrects
    it. Matching is fingerprint-based (see _is_our_message) so LinkedIn's
    rendering drift can't sneak an echo through as a fresh inbound.
    """
    fixed = 0
    for b in bubbles:
        if b.get("role") == "them" and _is_our_message(b.get("text"), our_fps):
            b["role"] = "us"
            fixed += 1
    return fixed


# ---------------------------------------------------------------------------
# Reply classifier — LIGHT_QUESTION | INTERESTED | NOT_INTERESTED_NEUTRAL
# ---------------------------------------------------------------------------

class ReplyClassifier:
    """
    Classifies the prospect's latest inbound message so the handler can decide
    whether to answer, hand off, or close politely.

    Keyword fast-path runs first (cheap, deterministic, catches the highest-stakes
    case even if the LLM is unavailable). Falls through to an LLM classification
    call otherwise. On any classifier error, defaults to INTERESTED — i.e. leans
    toward handoff rather than risking the bot over-chatting an engaged prospect.
    """

    def __init__(self, llm_config) -> None:
        self._llm_config = llm_config

    def classify(self, thread: list[dict], latest_message: str) -> Tuple[str, str]:
        fastpath = self._keyword_fastpath(latest_message)
        if fastpath:
            return fastpath

        try:
            system_prompt = render_prompt("reply_classifier_system")
            thread_str = "\n".join(
                f"[{'us' if m.get('role') == 'us' else 'prospect'}]: {m.get('text', '')}"
                for m in thread[-10:]
            )
            user_prompt = (
                f"Conversation so far:\n\n{thread_str}\n\n"
                f"Prospect's newest message:\n{latest_message}"
            )
            raw = llm_complete(
                system=system_prompt,
                user=user_prompt,
                config=self._llm_config,
                caller="reply_classifier",
            ).strip()
            return self._parse(raw)
        except Exception as exc:
            logger.warning("Classifier LLM call failed (%s) — defaulting to INTERESTED (handoff-lean).", exc)
            return "INTERESTED", f"classifier_error:{exc}"

    def classify_new_messages(
        self, thread: list[dict], messages: list[dict]
    ) -> Tuple[str, str]:
        """
        Classify EVERY new inbound message, not just the newest one.

        LinkedIn hands us several messages at once (and the first sweep of an
        existing thread imports the whole backlog), so classifying only the
        newest silently discards intent expressed in any earlier one. An
        explicit "yes tell me more" sitting behind a trailing "what's your
        process?" would never reach the handoff path — the prospect declares
        interest and the bot answers the question instead of handing off.

        Precedence:
          - A hard decline in the NEWEST message wins outright: it is the
            prospect's most recent intent and must be respected.
          - Otherwise INTERESTED anywhere in the batch escalates, matching this
            classifier's documented handoff-lean bias. Over-handing-off costs a
            human one glance at the thread; under-handing-off loses the lead.
          - Otherwise the newest message's own label stands.
        """
        texts = [(m.get("text") or "").strip() for m in messages]
        texts = [t for t in texts if t]
        if not texts:
            return "LIGHT_QUESTION", "no_text_to_classify"

        results = [self.classify(thread, t) for t in texts]
        newest_label, newest_reason = results[-1]

        if newest_label == "NOT_INTERESTED":
            return newest_label, newest_reason

        for idx, (label, reason) in enumerate(results):
            if label == "INTERESTED":
                if idx == len(results) - 1:
                    return label, reason
                logger.info(
                    "[CLASSIFY] Interest found in message %d of %d (%r) — escalating to handoff.",
                    idx + 1, len(results), texts[idx][:60],
                )
                return label, f"interest_in_earlier_message[{idx + 1}/{len(results)}]:{reason}"

        return newest_label, newest_reason

    def _keyword_fastpath(self, text: str) -> Optional[Tuple[str, str]]:
        low = (text or "").lower()
        interested_m = _INTERESTED_RE.search(low)
        declined_m = _NOT_INTERESTED_RE.search(low)
        interested_hit = interested_m.group(0) if interested_m else None
        declined_hit = declined_m.group(0) if declined_m else None
        if interested_hit and declined_hit:
            # Mixed signals ("what's the cost? otherwise not interested") — let the LLM decide.
            return None
        if declined_hit:
            return "NOT_INTERESTED", f"keyword_fastpath:{declined_hit!r}"
        if interested_hit:
            return "INTERESTED", f"keyword_fastpath:{interested_hit!r}"
        return None

    def _parse(self, raw: str) -> Tuple[str, str]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        try:
            data = json.loads(cleaned)
            label = str(data.get("label", "")).strip().upper()
            reason = str(data.get("reason", "")).strip() or "no_reason_given"
            if label in _CLASSIFY_LABELS:
                return label, reason
        except Exception:
            pass
        logger.warning("Classifier returned unparseable output (%r) — defaulting to INTERESTED.", raw[:200])
        return "INTERESTED", "classifier_unparseable_default_handoff"


# ---------------------------------------------------------------------------
# Gemini reply generator
# ---------------------------------------------------------------------------

_INTENT_NOTES = {
    "LIGHT_QUESTION": (
        "The prospect just asked a light question about the process or the company. "
        "Answer it concisely and only with facts present in the VERIFIED COMPANY FACTS block "
        "(if any relevant facts are present) — then stop. Do not pitch further or ask for a call."
    ),
    "NOT_INTERESTED_NEUTRAL": (
        "The prospect's message shows no strong signal, or a mild decline. Reply briefly and "
        "politely, leave the door open for later, and do not chase or re-pitch."
    ),
    "NOT_INTERESTED": (
        "The prospect has clearly declined. Send ONE short, gracious closing line — thank them "
        "for their time, no pitch, no questions, no attempt to change their mind. This is the "
        "final message in this conversation."
    ),
    # Live-session-only intents — used while the prospect is chatting in real time.
    "LIVE_DISCOVERY": (
        "You are mid live chat and this prospect has shown real interest. A human teammate has "
        "already been alerted internally — NEVER mention that to the prospect; you keep the "
        "conversation flowing naturally yourself. Shift into light discovery: learn what roles "
        "they're hiring for, roughly how many, and how soon — ONE short question at a time, "
        "woven naturally into the conversation. No pitching, no pricing, no proposing calls."
    ),
    "LIVE_POLITE": (
        "You are mid live chat and the prospect has indicated they're not interested, but they "
        "are still talking. Stay warm, human, and brief — respond to what they actually said, "
        "no pitching, no convincing, no guilt. Let the conversation wind down naturally."
    ),
}


# ---------------------------------------------------------------------------
# Reply quality gate
# ---------------------------------------------------------------------------
# Deterministic checks every outbound reply must pass before it is sent.
# Catches the failure modes that make a message read as AI/spam or violate
# policy (meeting proposals, pricing talk) even when the LLM ignores its prompt.

_QC_BANNED_PHRASES = (
    "finds you well", "hope you're doing well", "hope you are doing well",
    "just following up", "just checking in", "i came across your profile",
    "i'd love to", "i would love to", "great question", "absolutely!", "certainly",
    "delve", "leverage", "seamless", "streamline", "game-changer", "cutting-edge",
    "circle back", "touch base", "at your earliest convenience", "don't hesitate",
    "do not hesitate", "i understand your concern", "as an ai",
)

# Phrases that read as US proposing a meeting/call. The bot's only job is to
# gauge interest — humans take over anything worth a meeting.
_QC_MEETING_PROPOSAL_RE = re.compile(
    r"(let'?s\s+(talk|chat|connect|meet)"
    r"|(shall|can|could|should)\s+we\s+[^.?!]{0,30}?(call|meet|chat)"
    r"|(hop|jump|get)\s+on\s+a\s+(call|zoom|meet)"
    r"|(quick|short|brief)\s+(call|chat|meeting)"
    r"|schedule\s+a\b|book\s+a\b|are\s+you\s+(free|available)"
    r"|grab\s+(a\s+)?coffee|my\s+calendar|calendly)",
    re.IGNORECASE,
)

# Pricing/commercial terms leaking into an outbound reply.
_QC_PRICING_RE = re.compile(
    r"([$₹€£]\s?\d|\d+\s?%|\bper\s+hire\b|\bour\s+(rate|fee|pricing)s?\b)",
    re.IGNORECASE,
)

_QC_MARKDOWN_RE = re.compile(r"(\*\*|__|^#+\s|^[-*]\s)", re.MULTILINE)
_QC_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿]")


def quality_check_reply(text: str, max_chars: int) -> list[str]:
    """Return a list of human-readable issues; an empty list means the reply passes."""
    issues: list[str] = []
    stripped = (text or "").strip()
    if not stripped:
        return ["empty reply"]
    low = stripped.lower()

    if len(stripped) > max_chars:
        issues.append(f"too long ({len(stripped)} chars, cap {max_chars}) — write 1-2 short sentences")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", stripped) if s.strip()]
    if len(sentences) > 3:
        issues.append(f"too many sentences ({len(sentences)}) — the hard limit is 2")
    for phrase in _QC_BANNED_PHRASES:
        if phrase in low:
            issues.append(f"banned AI/spam phrase: {phrase!r}")
    if _QC_MEETING_PROPOSAL_RE.search(stripped):
        issues.append("proposes a call/meeting — never do this; only gauge interest")
    if _QC_PRICING_RE.search(stripped):
        issues.append("mentions pricing/commercial terms — never discuss these")
    if _QC_MARKDOWN_RE.search(stripped):
        issues.append("contains markdown formatting — plain text only")
    if _QC_EMOJI_RE.search(stripped):
        issues.append("contains emoji — not allowed")
    return issues


class LLMReplyGenerator:
    """
    Generates a LinkedIn reply using the project's shared LLM client.
    Reads provider/model/key from AppConfig.llm — same config as dossier generator.
    Supports openai, gemini, groq via LLM_PROVIDER in .env.

    Voice, tone, and hard constraints (persona integrity, no pricing, no fabrication)
    are centralized in prompts/reply_voice_rules.txt — the single source of truth for
    HOW the bot talks. This method only supplies WHAT to talk about.

    If a Retriever is supplied, the prospect's latest message is used as a
    RAG query and the retrieved company facts are injected into the prompt as
    a VERIFIED COMPANY FACTS block. If retrieval returns nothing or the
    Retriever is absent, behaviour falls back to the voice rules alone —
    the bot never fabricates facts either way; it just has less to work with.
    """

    def __init__(self, config: AppConfig, retriever: Optional["Retriever"] = None) -> None:
        self._llm_config = config.llm
        self._retriever  = retriever

    def generate_reply(
        self,
        thread: list[dict],
        prospect_name: str,
        prospect_title: str,
        company_name: str,
        recruiter_name: str,
        agency_name: str,
        classification_label: str = "",
        critique: str = "",
    ) -> str:
        thread_lines = []
        for msg in thread:
            role_label = recruiter_name if msg["role"] == "us" else prospect_name
            thread_lines.append(f"[{role_label}]: {msg['text']}")

        thread_str = "\n\n".join(thread_lines)

        system_prompt = render_prompt(
            "reply_voice_rules",
            recruiter_name=recruiter_name,
            agency_name=agency_name,
        )

        # RAG: retrieve facts grounded in the prospect's latest message
        facts_block = self._build_facts_block(thread)
        intent_note = _INTENT_NOTES.get(classification_label, "")

        critique_note = ""
        if critique:
            critique_note = (
                "\nIMPORTANT — your previous draft was rejected by quality review for these "
                f"reasons: {critique}. Write a fresh reply that avoids every one of them.\n"
            )

        prompt = f"""
You are messaging {prospect_name} ({prospect_title}) at {company_name} on LinkedIn.
{intent_note}
{critique_note}{facts_block}
Here is the conversation so far:

{thread_str}

Write your next reply to {prospect_name}. Do not start with "Hi {prospect_name}" every time —
vary your openers, and don't repeat a phrase you've already used earlier in this thread.
""".strip()

        try:
            reply = llm_complete(
                system=system_prompt,
                user=prompt,
                config=self._llm_config,
                caller="reply_handler",
            ).strip()
            if not reply:
                raise ValueError("LLM returned empty response.")
            return reply
        except Exception as exc:
            logger.error("Reply generation failed: %s", exc)
            raise

    def _build_facts_block(self, thread: list[dict]) -> str:
        """
        Query the RAG store with the prospect's latest message.
        Returns a formatted facts block string, or "" if nothing retrieved.
        """
        if self._retriever is None:
            return ""

        # Use the most recent inbound message as the retrieval query
        their_msgs = [m for m in thread if m.get("role") == "them"]
        if not their_msgs:
            return ""
        query = their_msgs[-1].get("text", "").strip()
        if not query:
            return ""

        try:
            chunks = self._retriever.retrieve(query)
        except Exception as exc:
            logger.warning("RAG retrieval error (skipping): %s", exc)
            return ""

        if not chunks:
            return ""

        facts_lines = []
        seen = set()
        for chunk in chunks:
            text = chunk.get("text", "").strip()
            if text and text not in seen:
                facts_lines.append(text)
                seen.add(text)

        if not facts_lines:
            return ""

        facts_text = "\n\n".join(facts_lines)
        logger.info("RAG injecting %d fact chunk(s) for query: %r", len(facts_lines), query[:60])
        return f"""
--- VERIFIED COMPANY FACTS (use ONLY these — do not invent anything not listed here) ---
{facts_text}
--- END VERIFIED COMPANY FACTS ---

"""


# ---------------------------------------------------------------------------
# Thread scraper
# ---------------------------------------------------------------------------

class LinkedInThreadScraper:
    """
    Opens the LinkedIn message overlay for a given profile and scrapes
    the full visible thread.

    Returns a list of dicts: {"role": "us"|"them", "text": "...", "ts": "..."?}
    "ts" is populated with a best-effort real timestamp when the DOM exposes one
    (a `time[datetime]` element, or a relative "X minutes ago" style label found
    while walking up the bubble's ancestors). This is unverified against a live
    LinkedIn DOM snapshot — if LinkedIn's markup doesn't expose it, "ts" is simply
    omitted and callers fall back to a wall-clock proxy (see _find_new_their_messages).
    The caller is responsible for comparing with stored thread to find new messages.
    """

    def __init__(self, browser: LinkedInBrowser, my_name: str) -> None:
        self._browser  = browser
        self._my_name  = my_name.strip().lower()   # used to classify "us" vs "them"

    def scrape_thread(self, profile_url: str) -> List[dict]:
        """
        Navigate to the prospect's LinkedIn profile, open the message thread
        (overlay or full-page — whichever LinkedIn gives us), and scrape it.

        Returns [] if the thread could not be opened or no messages found.
        """
        page = self._browser.page
        if page is None:
            raise RuntimeError("Browser not started.")

        logger.info("[SCRAPE] Navigating to profile: %s", profile_url)
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
        except Exception as exc:
            logger.warning("[SCRAPE] Failed to navigate to %s: %s", profile_url, exc)
            return []

        # Find the Message link — handles both new-conv (compose URL) and existing
        # threads (thread URL, which LinkedIn uses once a conversation exists).
        msg_link = self._first_visible(page, _MSG_LINK_SELS, timeout=6000)
        if msg_link is None:
            logger.warning("[SCRAPE] Message link not found on %s", profile_url)
            return []

        # Capture href before any click attempt so we can fall back to direct nav
        href = ""
        try:
            href = msg_link.get_attribute("href") or ""
            if href.startswith("/"):
                href = "https://www.linkedin.com" + href
            logger.info("[SCRAPE] Message link href: %s", href)
        except Exception:
            pass

        # Dismiss any popup/overlay that might intercept the click
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception:
            pass

        # Attempt 1: direct href navigation (most reliable — avoids click interception entirely)
        opened = False
        if href:
            try:
                logger.info("[SCRAPE] Navigating directly to messaging URL: %s", href)
                page.goto(href, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1000)
                opened = True
            except Exception as exc:
                logger.warning("[SCRAPE] Direct URL navigation failed: %s", exc)

            if opened and _is_promo_page(page):
                logger.warning("[SCRAPE] Direct href navigation landed on a promo page.")
                opened = False
                _recover_from_promo(page, profile_url, logger)

        # Attempt 2: JS click (bypasses pointer-event interception entirely, unlike force click)
        if not opened:
            try:
                msg_link.evaluate("el => el.click()")
                page.wait_for_timeout(1000)
                opened = True
                logger.info("[SCRAPE] Thread opened via JS click.")
            except Exception as exc:
                logger.warning("[SCRAPE] JS click failed: %s", exc)

            if opened and _is_promo_page(page):
                logger.warning("[SCRAPE] JS click landed on a promo page.")
                opened = False
                _recover_from_promo(page, profile_url, logger)

        # Attempt 3: normal click (last resort)
        if not opened:
            try:
                msg_link.click(timeout=4000)
                opened = True
                logger.info("[SCRAPE] Thread opened via normal click.")
            except Exception as exc:
                logger.warning("[SCRAPE] Normal click failed: %s", exc)

            if opened and _is_promo_page(page):
                logger.warning("[SCRAPE] Normal click landed on a promo page.")
                opened = False
                _recover_from_promo(page, profile_url, logger)

        # Attempt 4: force click (final fallback)
        if not opened:
            try:
                msg_link.click(timeout=4000, force=True)
                opened = True
                logger.info("[SCRAPE] Thread opened via force click.")
            except Exception as exc:
                logger.warning("[SCRAPE] Force click failed: %s", exc)

            if opened and _is_promo_page(page):
                logger.warning("[SCRAPE] Force click landed on a promo page.")
                opened = False
                _recover_from_promo(page, profile_url, logger)

        if not opened:
            logger.warning("[SCRAPE] Could not open Message thread for %s", profile_url)
            return []

        page.wait_for_timeout(1500)

        # Wait for EITHER the overlay (click from profile page) OR the full messaging
        # page (direct URL navigation to /messaging/thread/...).  The overlay check is
        # tried first; on timeout we fall through to the full-page check.
        in_overlay = False
        try:
            page.wait_for_selector(_MSG_OVERLAY_SEL, state="visible", timeout=5000)
            in_overlay = True
            logger.info("[SCRAPE] Messaging overlay detected.")
        except PlaywrightTimeoutError:
            pass

        if not in_overlay:
            # On the full messaging page the message list items are still present;
            # just wait for the first bubble to appear.
            try:
                page.wait_for_selector(_MSG_BUBBLE_SEL, state="visible", timeout=6000)
                logger.info("[SCRAPE] Full messaging page detected (no overlay).")
            except PlaywrightTimeoutError:
                logger.warning(
                    "[SCRAPE] Neither overlay nor message list appeared for %s", profile_url
                )
                logger.warning("[SCRAPE] Current URL at failure: %s", page.url)
                try:
                    page.screenshot(path="debug_scrape_failure.png")
                    logger.warning("[SCRAPE] Saved screenshot to debug_scrape_failure.png")
                except Exception as exc:
                    logger.warning("[SCRAPE] Could not save screenshot: %s", exc)
                return []

        # Give messages time to fully render
        page.wait_for_timeout(1200)

        # Scroll to top to expose older messages
        scroll_sel = _MSG_OVERLAY_SEL if in_overlay else _MSG_FULLPAGE_SELS[0]
        try:
            container = page.locator(scroll_sel).first
            page.evaluate("(el) => { el.scrollTop = 0; }", container.element_handle())
            page.wait_for_timeout(800)
        except Exception:
            pass

        thread = self._extract_bubbles(page)
        logger.info("[SCRAPE] Scraped %d message(s) from thread.", len(thread))

        # Close overlay so it doesn't block navigation to the next profile
        if in_overlay:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
            except Exception:
                pass

        return thread

    def _extract_bubbles(self, page: Page) -> List[dict]:
        """
        Extract all visible message bubbles from the overlay.
        Returns list of {"role": "us"|"them", "text": "...", "ts": "..."?} in chronological order.
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
                    msg = {"role": role, "text": text}
                    ts = self._extract_bubble_ts(item, page)
                    if ts:
                        msg["ts"] = ts
                    thread.append(msg)
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("Error extracting message bubbles: %s", exc)

        return thread

    def _classify_sender(self, item, page: Page) -> str:
        """
        Classify whether this message bubble was sent by us or by them.

        LinkedIn now uses obfuscated/hashed CSS class names, so we cannot rely on
        semantic names like 'msg-s-message-list__event--right'.  Instead we use:
          1. Semantic class name check (still works on some LinkedIn versions)
          2. Computed CSS: our messages have alignSelf=flex-end on some ancestor
          3. Aria-label patterns: LinkedIn adds 'You sent' / 'Your message' labels
          4. MY_LINKEDIN_NAME in aria-label as a final fallback
        """
        try:
            is_ours = page.evaluate(
                """([el, myName]) => {
                    let node = el;
                    for (let depth = 0; depth < 15 && node && node !== document.body; depth++) {
                        const cls = (node.className || '').toString();
                        // Semantic class names (pre-obfuscation LinkedIn, still checked)
                        if (cls.includes('msg-s-message-list__event--right')) return true;
                        if (cls.includes('msg-s-event-listitem--message-from-you')) return true;

                        // Aria-label patterns LinkedIn uses for accessibility
                        const aria = (node.getAttribute('aria-label') || '').toLowerCase();
                        if (aria.startsWith('you sent') || aria.startsWith('you:') ||
                            aria.includes('your message') || aria.includes('sent by you')) return true;
                        if (myName && aria.includes(myName)) return true;

                        // Computed layout (works on some LinkedIn versions)
                        try {
                            const s = window.getComputedStyle(node);
                            if (s.alignSelf === 'flex-end') return true;
                        } catch (e) {}

                        node = node.parentElement;
                    }
                    return false;
                }""",
                [item.element_handle(), self._my_name.lower()],
            )
            if is_ours:
                return "us"

            return "them"
        except Exception:
            return "them"  # safe default — treat unknown as inbound

    def _extract_bubble_ts(self, item, page: Page) -> Optional[str]:
        """
        Best-effort extraction of a real send timestamp for this bubble, used to
        drive live-vs-batch detection more accurately than a wall-clock proxy.

        Looks for a `time[datetime]` element (ISO-ish attribute, most reliable),
        then a bare `<time>` text node walking up to 6 ancestor levels. Text is
        parsed as a relative label ("5 minutes ago", "just now"); absolute
        clock-only text ("10:32 AM", no date) is intentionally NOT trusted since
        it can't be reliably converted to a UTC instant. Returns None on any
        failure so the caller falls back to a wall-clock proxy — this extraction
        is unverified against a live LinkedIn DOM snapshot and may simply find
        nothing, which is a safe, expected outcome.
        """
        try:
            raw = page.evaluate(
                """(el) => {
                    let node = el;
                    for (let depth = 0; depth < 6 && node && node !== document.body; depth++) {
                        if (node.querySelector) {
                            const withAttr = node.querySelector('time[datetime]');
                            if (withAttr) {
                                const dt = withAttr.getAttribute('datetime');
                                if (dt) return 'ATTR:' + dt;
                            }
                            const bare = node.querySelector('time');
                            if (bare && bare.textContent && bare.textContent.trim()) {
                                return 'TEXT:' + bare.textContent.trim();
                            }
                        }
                        node = node.parentElement;
                    }
                    return null;
                }""",
                item.element_handle(),
            )
        except Exception:
            return None

        if not raw:
            return None

        if raw.startswith("ATTR:"):
            dt = _parse_ts(raw[5:])
            return dt.isoformat() if dt else None

        if raw.startswith("TEXT:"):
            return self._parse_relative_text(raw[5:])

        return None

    @staticmethod
    def _parse_relative_text(text: str) -> Optional[str]:
        low = text.strip().lower()
        now = datetime.now(timezone.utc)
        if low in ("now", "just now"):
            return now.isoformat()
        m = re.match(r"(\d+)\s*(second|minute|hour|day|week)s?\s*ago", low)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            delta = {
                "second": timedelta(seconds=n),
                "minute": timedelta(minutes=n),
                "hour": timedelta(hours=n),
                "day": timedelta(days=n),
                "week": timedelta(weeks=n),
            }[unit]
            return (now - delta).isoformat()
        # Absolute clock-only text (e.g. "10:32 AM") has no reliable date component —
        # deliberately not trusted here. Caller falls back to the wall-clock proxy.
        return None

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

    Types the reply character-by-character into the focused compose box (real
    keydown/keyup events per keystroke, not a paste/fill injection) so LinkedIn's
    "…is typing" indicator has a chance to fire on the other side. Whether
    LinkedIn actually broadcasts that indicator for this compose surface has not
    been independently verified (would require a second logged-in account
    watching the thread live) — this sends real per-keystroke events regardless,
    since the human-paced typing cadence is valuable on its own even if the
    indicator itself doesn't show.
    """

    def __init__(self, browser: LinkedInBrowser) -> None:
        self._browser = browser

    def send(self, text: str, *, policy: Optional[ReplyPolicyConfig] = None) -> None:
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

        if policy is not None:
            self._type_humanized(page, text, policy)
        else:
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

    def _type_humanized(self, page: Page, text: str, policy: ReplyPolicyConfig) -> None:
        """
        Type `text` one character at a time into the currently-focused compose box,
        with a jittered per-keystroke delay and a longer pause after punctuation.
        Fires real keyboard events (page.keyboard.type per character) rather than
        setting the field value directly, so LinkedIn sees genuine keystrokes.
        """
        start = time.monotonic()
        for ch in text:
            page.keyboard.type(ch)
            delay_ms = max(20.0, random.gauss(policy.typing_ms_per_char, policy.typing_jitter_ms))
            page.wait_for_timeout(delay_ms)
            if ch in ".,!?":
                page.wait_for_timeout(policy.typing_punct_pause_ms)
        elapsed = time.monotonic() - start
        logger.info(
            "[TYPING] Typed %d chars over %.1fs (human-paced). "
            "Whether LinkedIn's 'typing…' indicator broadcasts for this compose "
            "surface has not been independently confirmed — verify manually if needed.",
            len(text), elapsed,
        )


# ---------------------------------------------------------------------------
# Manager handoff notification
# ---------------------------------------------------------------------------

def _send_handoff_email(
    cfg: AppConfig,
    *,
    prospect: ProspectORM,
    profile_url: str,
    company_name: str,
    thread: list[dict],
    latest_message: str,
    reason: str,
    live: bool = False,
) -> Tuple[bool, Optional[str]]:
    """
    Notify the manager that a conversation was handed off to a human.
    Returns (success, error_message_or_None). Never raises — callers must still
    record handoff regardless of email outcome; only the email itself is best-effort.

    live=True → the prospect is chatting in real time RIGHT NOW: the subject is
    flagged [LIVE], the body tells the manager they can take over immediately,
    and notes that the bot keeps light discovery going until the prospect leaves.
    """
    dispatch = cfg.outreach_dispatch
    manager_email = dispatch.handoff_manager_email
    if not manager_email:
        return False, "REPLY_HANDOFF_MANAGER_EMAIL is not configured."
    if not dispatch.smtp_host:
        return False, "SMTP_HOST is not configured."

    sender = dispatch.smtp_from_email or dispatch.smtp_username or "noreply@localhost"

    prospect_name = prospect.name or "Unknown prospect"
    prospect_title = prospect.current_title or prospect.headline or "Unknown"
    low_reason = reason.lower()
    is_interested = "interested" in low_reason and "not_interested" not in low_reason
    verdict = "INTERESTED LEAD" if is_interested else "NEEDS A HUMAN"
    prefix = "[Handoff][LIVE]" if live else "[Handoff]"
    subject = f"{prefix} {verdict}: {prospect_name} @ {company_name}"

    our_count = sum(1 for m in thread if m.get("role") == "us")
    their_count = sum(1 for m in thread if m.get("role") == "them")

    def _msg_ts(m: dict) -> str:
        dt = _parse_ts(m.get("ts"))
        return dt.strftime("%d %b %H:%M") if dt else "--"

    # ── Plain-text part: scannable top-down — verdict, who, why, latest, thread ──
    text_thread_lines = [
        f"  [{_msg_ts(m):>12}]  {'Us  ' if m.get('role') == 'us' else 'Them'}: {m.get('text', '')}"
        for m in thread
    ]
    if live:
        status_lines = [
            ">>> PROSPECT IS ONLINE AND CHATTING RIGHT NOW — you can take over immediately.",
            ">>> Until you do, the bot keeps light discovery going (roles, headcount, timeline)",
            ">>> so the prospect is never left waiting. Check the dashboard for the live transcript.",
        ]
    else:
        status_lines = ["The bot has gone silent on this thread. It is now yours."]

    body = "\n".join(
        [
            f"LEAD HANDOFF — {verdict}",
            *status_lines,
            "",
            "PROSPECT",
            f"  Name    : {prospect_name}",
            f"  Title   : {prospect_title}",
            f"  Company : {company_name}",
            f"  Profile : {profile_url}",
            "",
            "WHY THIS LANDED IN YOUR INBOX",
            f"  {reason}",
            "",
            f"LATEST MESSAGE FROM {prospect_name.upper()}",
            f"  \"{latest_message}\"",
            "",
            f"FULL CONVERSATION  ({our_count} sent / {their_count} received, oldest first)",
            "\n".join(text_thread_lines) if text_thread_lines else "  (empty)",
            "",
            "NEXT STEP",
            (
                "  Open LinkedIn NOW and take over the live chat — the bot steps aside the "
                "moment the session ends." if live else
                "  Reply manually from LinkedIn. The bot will not touch this thread again."
            ),
        ]
    )

    # ── HTML part: chat-style transcript so the manager can absorb the thread fast ──
    accent = "#16a34a" if is_interested else "#d97706"
    live_banner = ""
    if live:
        live_banner = (
            "<div style='margin:0 0 14px;padding:12px 16px;border-radius:8px;"
            "background:#16a34a;color:#ffffff;font-size:14px;font-weight:700;'>"
            "&#9679; LIVE — prospect is online and chatting with the bot right now. "
            "Take over immediately; the bot holds light discovery until you do."
            "</div>"
        )
    bubble_rows = []
    for m in thread:
        is_us = m.get("role") == "us"
        who = "Us" if is_us else (prospect_name.split()[0] if prospect_name.strip() else "Them")
        align = "right" if is_us else "left"
        bg = "#0a66c2" if is_us else "#f3f4f6"
        fg = "#ffffff" if is_us else "#111827"
        bubble_rows.append(
            f"<tr><td style='padding:3px 0;text-align:{align};'>"
            f"<div style='display:inline-block;max-width:80%;background:{bg};color:{fg};"
            f"border-radius:12px;padding:8px 12px;text-align:left;font-size:14px;line-height:1.45;'>"
            f"{html.escape(m.get('text', ''))}</div>"
            f"<div style='font-size:11px;color:#9ca3af;margin-top:2px;'>{who} · {_msg_ts(m)}</div>"
            f"</td></tr>"
        )
    html_body = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:640px;margin:0 auto;color:#111827;">
  {live_banner}
  <div style="border-left:4px solid {accent};background:#f9fafb;padding:14px 18px;border-radius:0 8px 8px 0;">
    <div style="font-size:12px;font-weight:700;letter-spacing:1px;color:{accent};">LEAD HANDOFF — {html.escape(verdict)}</div>
    <div style="font-size:19px;font-weight:700;margin-top:4px;">{html.escape(prospect_name)}
      <span style="font-weight:400;color:#6b7280;">@ {html.escape(company_name)}</span></div>
    <div style="font-size:13px;color:#6b7280;margin-top:2px;">{html.escape(prospect_title)}</div>
    <div style="font-size:12px;color:#6b7280;margin-top:8px;">Reason: <code style="background:#eef2f7;padding:1px 6px;border-radius:4px;">{html.escape(reason)}</code></div>
  </div>

  <div style="margin:16px 0;padding:12px 16px;border:1px solid #e5e7eb;border-radius:8px;background:#fffbeb;">
    <div style="font-size:11px;font-weight:700;letter-spacing:1px;color:#92400e;margin-bottom:6px;">LATEST FROM {html.escape(prospect_name.upper())}</div>
    <div style="font-size:15px;line-height:1.5;">&ldquo;{html.escape(latest_message)}&rdquo;</div>
  </div>

  <div style="font-size:11px;font-weight:700;letter-spacing:1px;color:#6b7280;margin:14px 0 6px;">
    FULL CONVERSATION &nbsp;·&nbsp; {our_count} sent / {their_count} received (oldest first)</div>
  <table style="width:100%;border-collapse:collapse;">{''.join(bubble_rows) or "<tr><td style='color:#9ca3af;'>(empty)</td></tr>"}</table>

  <div style="margin-top:18px;padding-top:12px;border-top:1px solid #e5e7eb;font-size:13px;">
    <a href="{html.escape(profile_url)}" style="display:inline-block;background:#0a66c2;color:#ffffff;text-decoration:none;padding:9px 16px;border-radius:6px;font-weight:600;">Open LinkedIn profile &amp; reply</a>
    <div style="color:#6b7280;margin-top:10px;">The bot has gone silent on this thread — reply manually from LinkedIn.</div>
  </div>
</div>"""

    email = EmailMessage()
    email["From"] = sender
    email["To"] = manager_email
    email["Subject"] = subject
    email.set_content(body)
    email.add_alternative(html_body, subtype="html")

    try:
        if dispatch.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                dispatch.smtp_host, dispatch.smtp_port, timeout=dispatch.smtp_timeout_seconds
            ) as smtp:
                if dispatch.smtp_username:
                    smtp.login(dispatch.smtp_username, dispatch.smtp_password or "")
                smtp.send_message(email)
        else:
            with smtplib.SMTP(
                dispatch.smtp_host, dispatch.smtp_port, timeout=dispatch.smtp_timeout_seconds
            ) as smtp:
                smtp.ehlo()
                if dispatch.smtp_use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                if dispatch.smtp_username:
                    smtp.login(dispatch.smtp_username, dispatch.smtp_password or "")
                smtp.send_message(email)
        logger.info("[HANDOFF] Notification email sent to %s for prospect %d.", manager_email, prospect.id)
        return True, None
    except Exception as exc:
        logger.error("[HANDOFF] Failed to send notification email for prospect %d: %s", prospect.id, exc)
        return False, str(exc)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class LinkedInReplyHandler:
    """
    Orchestrates one full polling cycle:
      Load active conversations → scrape each thread → detect new replies
      → classify → answer / handoff → send → update DB → bounded fast-follow
      for any conversation now running live.
    """

    def __init__(
        self,
        browser: LinkedInBrowser,
        config: AppConfig,
        agent_id: Optional[int] = None,
        max_workers: Optional[int] = None,
        _share_components_from: Optional["LinkedInReplyHandler"] = None,
    ) -> None:
        self._browser   = browser
        self._cfg       = config
        self._agent_id  = agent_id
        self._policy: ReplyPolicyConfig = config.reply_policy
        # None → use REPLY_MAX_CONCURRENT_WORKERS from config; explicit value wins.
        self._max_workers = max_workers

        if _share_components_from is not None:
            # Parallel worker: reuse the parent's generator/classifier — they are
            # stateless and thread-safe (llm_complete builds a fresh client per
            # call), and this avoids re-opening the RAG store once per thread.
            self._gemini     = _share_components_from._gemini
            self._classifier = _share_components_from._classifier
            # Live-session counters are shared with the parent so run() can
            # report totals across all workers.
            self._live_stats      = _share_components_from._live_stats
            self._live_stats_lock = _share_components_from._live_stats_lock
        else:
            self._live_stats      = {"sessions": 0, "replies": 0}
            self._live_stats_lock = threading.Lock()
            # RAG retriever — initialised lazily; failures are non-fatal
            _retriever: Optional[Retriever] = None
            try:
                _retriever = Retriever(rag_cfg=config.rag, llm_cfg=config.llm)
            except Exception as exc:
                logger.warning("RAG Retriever could not be initialised (replies will work without it): %s", exc)

            self._gemini     = LLMReplyGenerator(config, retriever=_retriever)
            self._classifier = ReplyClassifier(config.llm)

        # Your LinkedIn display name (used to classify "us" vs "them" in threads)
        # Set MY_LINKEDIN_NAME in .env  e.g.  MY_LINKEDIN_NAME="Advik Sharma"
        self._my_name        = getattr(config, "my_linkedin_name", "") or ""
        self._recruiter_name = config.outreach.recruiter_name or "Alex"
        self._agency_name    = config.outreach.agency_name    or "RecruitPro"

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
            "checked":         0,
            "new_replies":     0,
            "ai_replies_sent": 0,
            "handed_off":      0,
            "closed":          0,
            "deferred":        0,
            "errors":          0,
            "skipped":         0,
            "live_sessions":   0,
            "live_replies":    0,
        }

        with self._live_stats_lock:
            self._live_stats["sessions"] = 0
            self._live_stats["replies"]  = 0

        conversations = self._load_active_conversations()

        if not conversations:
            logger.info("ReplyHandler: no active conversations to check.")
            return stats

        workers = (
            self._max_workers
            if self._max_workers is not None
            else getattr(self._policy, "max_concurrent_workers", 1)
        )
        workers = max(1, min(int(workers), len(conversations)))

        logger.info(
            "ReplyHandler: checking %d active conversation(s) with %d worker(s).",
            len(conversations), workers,
        )

        if workers > 1:
            self._run_concurrent(conversations, stats, workers)
        else:
            for conv in conversations:
                stats["checked"] += 1
                try:
                    result = self._process_one(conv)
                    self._tally(stats, result)
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

        # ── Bounded fast-follow for any conversation that just went live ──
        live_ids = self._fetch_live_conversation_ids([c.id for c in conversations])
        if live_ids:
            logger.info("ReplyHandler: %d conversation(s) live — starting fast-follow.", len(live_ids))
            self._fast_follow(live_ids, stats)

        with self._live_stats_lock:
            stats["live_sessions"] = self._live_stats["sessions"]
            stats["live_replies"]  = self._live_stats["replies"]

        logger.info("ReplyHandler cycle complete. Stats: %s", stats)
        return stats

    @staticmethod
    def _tally(stats: dict, result: str) -> None:
        if result in ("replied", "replied_live"):
            stats["new_replies"] += 1
            stats["ai_replies_sent"] += 1
        elif result == "handed_off":
            stats["handed_off"] += 1
        elif result == "closed":
            stats["closed"] += 1
        elif result == "deferred":
            stats["deferred"] += 1
        elif result == "skipped":
            stats["skipped"] += 1
        # "no_new" -> nothing to tally

    # ------------------------------------------------------------------
    # Concurrent processing — N parallel tabs on the same Chrome
    # ------------------------------------------------------------------

    def _run_concurrent(
        self,
        conversations: List[LinkedInConversationORM],
        stats: dict,
        workers: int,
    ) -> None:
        """
        Process conversations with N parallel worker threads. Each worker holds
        its own Playwright instance + CDP connection and drives its own dedicated
        tab in the already-logged-in Chrome (Playwright's sync API is bound to
        one thread, so per-thread instances are mandatory, not optional).

        Scraping, classification, and LLM generation run fully in parallel;
        actual sends are serialised through _GLOBAL_SEND_LOCK so the
        account-wide minimum send spacing holds no matter how many tabs run.
        """
        work_q: "queue.Queue[LinkedInConversationORM]" = queue.Queue()
        for conv in conversations:
            work_q.put(conv)
        stats_lock = threading.Lock()

        def _worker(worker_idx: int) -> None:
            wb = LinkedInBrowser(self._cfg.chrome)
            try:
                wb.start(new_page=True)
            except Exception as exc:
                logger.error("[WORKER-%d] Could not open a dedicated tab: %s", worker_idx, exc)
                return
            try:
                handler = LinkedInReplyHandler(
                    browser=wb,
                    config=self._cfg,
                    agent_id=self._agent_id,
                    _share_components_from=self,
                )
                while True:
                    try:
                        conv = work_q.get_nowait()
                    except queue.Empty:
                        break
                    # Everything after taking an item is inside the per-item
                    # guard, so no code path between "taken" and "tallied" can
                    # drop a prospect silently.
                    try:
                        with stats_lock:
                            stats["checked"] += 1
                        result = handler._process_one(conv)
                        with stats_lock:
                            self._tally(stats, result)
                    except Exception as exc:
                        logger.exception(
                            "[WORKER-%d] Unhandled error processing conversation for prospect %d: %s",
                            worker_idx, conv.prospect_id, exc,
                        )
                        with stats_lock:
                            stats["errors"] += 1
                        try:
                            self._record_error(conv.id, str(exc))
                        except Exception:
                            logger.warning(
                                "[WORKER-%d] Could not record error for conversation %d.",
                                worker_idx, conv.id, exc_info=True,
                            )

                    # Human-like delay before this worker picks its next prospect
                    time.sleep(random.uniform(_INTER_PROSPECT_DELAY_MIN, _INTER_PROSPECT_DELAY_MAX))
            except Exception:
                # A worker must never die silently. Items it hasn't taken remain
                # in the queue and are drained by the surviving workers; the
                # loud log makes a repeatedly-dying worker visible in ops.
                logger.exception(
                    "[WORKER-%d] Worker crashed — surviving workers continue the queue.", worker_idx
                )
            finally:
                try:
                    wb.stop()
                except Exception:
                    pass
                logger.info("[WORKER-%d] Done.", worker_idx)

        threads = [
            threading.Thread(target=_worker, args=(i + 1,), name=f"reply-worker-{i + 1}", daemon=True)
            for i in range(workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # ------------------------------------------------------------------
    # Fast-follow: bounded re-poll of live conversations within this run
    # ------------------------------------------------------------------

    def _fast_follow(self, conv_ids: List[int], stats: dict) -> None:
        """
        Re-poll conversations that are in 'live' mode a few extra times before
        returning control to the scheduler, so an actively-engaged prospect gets
        a faster reply than waiting for the next ~10-minute Task Scheduler run.

        Bounded on BOTH axes — whichever hits first stops the loop:
          - live_fastfollow_max_iterations extra poll rounds
          - live_fastfollow_max_wall_sec wall-clock budget
        A conversation drops out of the loop as soon as it's handed off or its
        mode reverts to 'batch' (no new message within live_revert_minutes).
        """
        remaining = set(conv_ids)
        start = time.monotonic()
        iteration = 0

        while (
            remaining
            and iteration < self._policy.live_fastfollow_max_iterations
            and (time.monotonic() - start) < self._policy.live_fastfollow_max_wall_sec
        ):
            iteration += 1
            time.sleep(self._policy.live_fastfollow_poll_sec)

            for conv_id in list(remaining):
                if (time.monotonic() - start) >= self._policy.live_fastfollow_max_wall_sec:
                    break
                conv = self._load_conversation(conv_id)
                if conv is None or conv.conversation_status != "active":
                    remaining.discard(conv_id)
                    continue
                try:
                    result = self._process_one(conv)
                    self._tally(stats, result)
                except Exception as exc:
                    logger.exception("[FAST-FOLLOW] Error processing conversation %d: %s", conv_id, exc)
                    stats["errors"] += 1
                    self._record_error(conv_id, str(exc))

                refreshed = self._load_conversation(conv_id)
                if (
                    refreshed is None
                    or refreshed.conversation_status != "active"
                    or refreshed.conversation_mode != "live"
                ):
                    remaining.discard(conv_id)

        logger.info(
            "[FAST-FOLLOW] Complete after %d iteration(s), %.1fs elapsed.",
            iteration, time.monotonic() - start,
        )

    # ------------------------------------------------------------------
    # Single conversation processor
    # ------------------------------------------------------------------

    def _process_one(self, conv: LinkedInConversationORM) -> str:
        """
        Returns "replied" | "no_new" | "skipped" | "handed_off" | "deferred"
        """
        if conv.conversation_status == "handed_off":
            return "skipped"

        prospect = self._load_prospect(conv.prospect_id)
        if prospect is None:
            logger.warning("Prospect %d not found in DB — skipping.", conv.prospect_id)
            return "skipped"

        profile_url = conv.linkedin_profile_url
        logger.info(
            "Checking conversation for prospect %d (%s)",
            conv.prospect_id, prospect.name or profile_url,
        )

        now = datetime.now(timezone.utc)
        self._maybe_revert_live_mode(conv, now)

        # 1. Scrape current thread from LinkedIn
        live_thread = self._scraper.scrape_thread(profile_url)
        self._update_last_checked(conv.id)

        if not live_thread:
            logger.info(
                "[PROCESS] No messages scraped for prospect %d — overlay/thread empty or unreachable.",
                conv.prospect_id,
            )
            return "no_new"

        logger.info(
            "[PROCESS] Scraped %d message(s) for prospect %d.",
            len(live_thread), conv.prospect_id,
        )

        # 2. Compare with stored thread to find new messages from them
        stored_thread = conv.get_thread()

        # Correct the scraper's sender guesses against what we know we sent, so
        # our own outbound can never be counted as an inbound reply.
        corrected = _relabel_our_own_bubbles(live_thread, _our_text_set(stored_thread))
        if corrected:
            logger.info(
                "[PROCESS] Re-labelled %d scraped bubble(s) as ours — DOM sender detection "
                "misread our own message(s) as inbound.",
                corrected,
            )

        logger.info(
            "[PROCESS] Stored thread has %d message(s) (%d from them). Live has %d from them.",
            len(stored_thread),
            sum(1 for m in stored_thread if m.get("role") == "them"),
            sum(1 for m in live_thread if m.get("role") == "them"),
        )
        new_their_messages = self._find_new_their_messages(stored_thread, live_thread)

        if not new_their_messages:
            logger.info(
                "[PROCESS] No new inbound messages for prospect %d.", conv.prospect_id
            )
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

        company_name = self._get_company_name(prospect.company_research_id)
        latest_text = new_their_messages[-1]["text"]

        # 4. Live-vs-batch detection (best-effort: prefers a real reply timestamp
        #    scraped from the DOM, falls back to a wall-clock proxy otherwise)
        is_live = self._detect_live(conv, new_their_messages[-1], now)
        if is_live:
            conv.conversation_mode = "live"
            conv.last_live_detected_utc = now
            logger.info("[PROCESS] Conversation %d detected LIVE.", conv.id)

        # 5. Classify and decide: answer, close, defer, or hand off.
        #    Every new inbound is classified — intent in any of them counts, not
        #    just the last one to arrive.
        label, reason = self._classifier.classify_new_messages(updated_thread, new_their_messages)
        logger.info(
            "[PROCESS] Classified prospect %d (%d new message(s)) as %s (%s)",
            conv.prospect_id, len(new_their_messages), label, reason,
        )

        # Clear decline → gracious one-line close, then mark not_interested so
        # the poller stops spending time here and the dashboard shows the outcome.
        if label == "NOT_INTERESTED":
            return self._close_not_interested(
                conv, prospect, profile_url, company_name,
                updated_thread, new_their_messages,
                f"classified_not_interested:{reason}", now,
            )

        handoff_reason = self._decide_handoff(conv, label, reason)
        if handoff_reason:
            self._handoff(conv, prospect, profile_url, company_name, updated_thread, latest_text, handoff_reason, now)
            return "handed_off"

        # 6. Safety caps — defer WITHOUT persisting the inbound message. Because
        #    reply detection diffs live-vs-stored "them" counts, storing the
        #    inbound here would make it invisible next run and the prospect
        #    would never get an answer. Leaving the thread unpersisted means the
        #    next scheduled sweep re-detects the same message and retries.
        cap_reason = self._check_safety_caps(conv, now)
        if cap_reason:
            logger.warning(
                "[PROCESS] Deferring reply for prospect %d (%s) — will retry next sweep.",
                conv.prospect_id, cap_reason,
            )
            return "deferred"

        # 7. Live mode: simulate a natural read/think delay before replying
        if conv.conversation_mode == "live":
            think_delay = random.uniform(
                self._policy.live_think_delay_min_sec, self._policy.live_think_delay_max_sec
            )
            logger.info("[PROCESS] Live mode — waiting %.1fs (read/think delay) before replying.", think_delay)
            time.sleep(think_delay)

        # 8. Generate AI reply and run it through the deterministic quality gate
        reply_text = self._generate_quality_reply(
            thread       = updated_thread,
            prospect     = prospect,
            company_name = company_name,
            label        = label,
        )
        if reply_text is None:
            # Same retry semantics as the cap defer above: keep the inbound
            # UNpersisted so the next sweep re-detects it and tries again with
            # a fresh generation, instead of silently ghosting the prospect.
            logger.warning(
                "[QC] No reply passed the quality gate for prospect %d — will retry next sweep.",
                conv.prospect_id,
            )
            return "deferred"

        logger.info("Generated reply (%d chars): %s…", len(reply_text), reply_text[:80])

        # 9. Send under the global send lock. Parallel workers scrape and generate
        #    concurrently, but actual sends are serialised (with the account-wide
        #    spacing re-checked inside the lock) so N tabs never burst-send.
        with _GLOBAL_SEND_LOCK:
            gap = self._seconds_since_last_global_send(datetime.now(timezone.utc))
            if gap is not None and gap < self._policy.min_global_spacing_sec:
                wait = self._policy.min_global_spacing_sec - gap
                logger.info("[SEND] Waiting %.1fs to honor global send spacing.", wait)
                time.sleep(wait)

            # Re-open the conversation overlay and send the reply (human-paced typing)
            # (scrape_thread already closed it with Escape — reopen via profile)
            self._reopen_overlay(profile_url)
            self._sender.send(reply_text, policy=self._policy)

            # 10. Append reply to thread and persist while still holding the lock,
            #     so the next worker's spacing check sees this send.
            updated_thread.append({
                "role": "us",
                "text": reply_text,
                "ts":   datetime.now(timezone.utc).isoformat(),
            })
            self._persist_reply(
                conv, updated_thread, new_their_messages, label, is_live,
                datetime.now(timezone.utc),
            )

        # 11. Linger in the (still open) thread: if the prospect is at their
        #     keyboard, answer them in real time instead of making them wait
        #     for the next scheduler tick. Best-effort — a failure here never
        #     undoes the reply that was already sent and persisted above.
        live_replies = 0
        try:
            live_replies = self._linger_and_live_session(
                conv, prospect, profile_url, company_name, updated_thread,
            )
        except Exception as exc:
            logger.warning(
                "[LIVE] Live-session error for prospect %d (non-fatal): %s",
                conv.prospect_id, exc,
            )

        return "replied_live" if live_replies else "replied"

    # ------------------------------------------------------------------
    # Quality-gated reply generation
    # ------------------------------------------------------------------

    def _generate_quality_reply(
        self,
        *,
        thread: list[dict],
        prospect: ProspectORM,
        company_name: str,
        label: str,
    ) -> Optional[str]:
        """
        Generate a reply and enforce quality_check_reply() on it. On failure the
        QC issues are fed back to the LLM as a critique and the reply is
        regenerated (up to qc_max_regen extra attempts). Returns None when no
        acceptable reply could be produced — the caller defers rather than
        sending something that reads as AI or violates policy.
        """
        critique = ""
        attempts = 1 + max(0, int(getattr(self._policy, "qc_max_regen", 1)))
        for attempt in range(1, attempts + 1):
            reply = self._gemini.generate_reply(
                thread               = thread,
                prospect_name        = prospect.name or "there",
                prospect_title       = prospect.current_title or prospect.headline or "Hiring Manager",
                company_name         = company_name,
                recruiter_name       = self._recruiter_name,
                agency_name          = self._agency_name,
                classification_label = label,
                critique             = critique,
            ).strip()
            issues = quality_check_reply(reply, getattr(self._policy, "qc_max_chars", 320))
            if not issues:
                if attempt > 1:
                    logger.info("[QC] Reply passed the quality gate on attempt %d.", attempt)
                return reply
            logger.warning(
                "[QC] Attempt %d/%d failed the quality gate: %s | draft: %r",
                attempt, attempts, "; ".join(issues), reply[:120],
            )
            critique = "; ".join(issues)
        return None

    # ------------------------------------------------------------------
    # Not-interested close
    # ------------------------------------------------------------------

    def _close_not_interested(
        self,
        conv: LinkedInConversationORM,
        prospect: ProspectORM,
        profile_url: str,
        company_name: str,
        updated_thread: list[dict],
        new_their_msgs: list[dict],
        reason: str,
        now: datetime,
    ) -> str:
        """
        The prospect clearly declined. Best-effort send one short gracious
        closing line, then flip the conversation to not_interested / dead so the
        poller never touches it again and the dashboard shows the outcome.
        The close is best-effort: even if generating/sending it fails, the
        conversation is still marked not_interested.
        """
        close_text: Optional[str] = None
        try:
            close_text = self._generate_quality_reply(
                thread       = updated_thread,
                prospect     = prospect,
                company_name = company_name,
                label        = "NOT_INTERESTED",
            )
        except Exception as exc:
            logger.warning("[CLOSE] Could not generate closing line for prospect %d: %s", conv.prospect_id, exc)

        sent_close = False
        if close_text:
            try:
                with _GLOBAL_SEND_LOCK:
                    gap = self._seconds_since_last_global_send(datetime.now(timezone.utc))
                    if gap is not None and gap < self._policy.min_global_spacing_sec:
                        time.sleep(self._policy.min_global_spacing_sec - gap)
                    self._reopen_overlay(profile_url)
                    self._sender.send(close_text, policy=self._policy)
                    sent_close = True
            except Exception as exc:
                logger.warning("[CLOSE] Could not send closing line for prospect %d: %s", conv.prospect_id, exc)

        if sent_close and close_text:
            updated_thread.append({
                "role": "us",
                "text": close_text,
                "ts":   datetime.now(timezone.utc).isoformat(),
            })

        with session_scope() as session:
            row = session.query(LinkedInConversationORM).filter_by(id=conv.id).one()
            row.thread_json             = json.dumps(updated_thread)
            row.messages_received       = (row.messages_received or 0) + len(new_their_msgs)
            if sent_close:
                row.messages_sent         = (row.messages_sent or 0) + 1
                row.last_message_sent_utc = now
            row.last_reply_received_utc = now
            row.last_checked_utc        = now
            row.conversation_status     = "not_interested"
            row.lead_stage              = "dead"
            # Reuses the handoff_reason column to record WHY the conversation closed.
            row.handoff_reason          = reason[:255]

        logger.info("[CLOSE] Prospect %d marked not_interested (%s).", conv.prospect_id, reason)
        return "closed"

    # ------------------------------------------------------------------
    # Live session — real-time conversation with an online prospect
    # ------------------------------------------------------------------

    def _linger_and_live_session(
        self,
        conv: LinkedInConversationORM,
        prospect: ProspectORM,
        profile_url: str,
        company_name: str,
        thread: list[dict],
    ) -> int:
        """
        After sending a reply, stay in the open thread and watch for a response.

        ENTRY PHASE — presence is a hint, a reply is the commitment:
          green dot online → wait up to live_online_grace_sec (~2.5 min);
          offline/unknown  → wait live_offline_grace_sec (~75s).
          Grace expires with no reply → return 0 silently. No nudges, no
          "are you there?" — the conversation goes back to scheduler cadence
          and the next thing that happens is THEM replying, whenever that is.

        ENGAGED PHASE — once they reply, hold a real-time conversation:
          read buffer (5-15s, longer for longer messages) → classify →
          generate through the quality gate → send. Ends on
          live_session_silence_end_min of silence, presence dot gone +60s
          quiet, session message cap, or wall-clock cap.

        Session policy deliberately differs from batch:
          - INTERESTED     → manager email fires immediately (flagged LIVE) but
            the bot keeps talking in light discovery mode — never leaves them
            mid-chat. Conversation locks to handed_off only AFTER the session.
          - NOT_INTERESTED → no mid-chat close; stay warm until they leave,
            THEN the conversation is marked not_interested.
          - Live sends skip the 45s global spacing (the lock still serialises
            typing) and are flagged "live": true so they're excluded from the
            24h batch cap (see LinkedInConversationORM.count_our_messages_since).

        Returns the number of live replies sent (0 if never engaged).
        """
        page = self._browser.page
        policy = self._policy

        online = _prospect_appears_online(page)
        grace = policy.live_online_grace_sec if online else policy.live_offline_grace_sec
        logger.info(
            "[LIVE] Lingering up to %.0fs for a response (prospect appears %s).",
            grace, "ONLINE" if online else "offline/unknown",
        )

        try:
            baseline_total = page.locator(_MSG_BUBBLE_SEL).count()
        except Exception:
            return 0
        their_count = sum(1 for m in thread if m.get("role") == "them")

        session_start   = time.monotonic()
        deadline        = session_start + grace
        last_activity   = session_start
        engaged         = False
        session_replies = 0
        interested_flag = False
        declined_flag   = False
        email_ok        = False
        pending_tail    = 0   # inbound msgs appended to `thread` but not yet answered
        polls_since_presence_check = 0

        def _trim_pending() -> None:
            # Drop unanswered inbound from the in-memory thread so it is NOT
            # persisted — the next sweep re-detects it and retries the reply
            # (same retry semantics as batch defers). Answered exchanges were
            # already persisted incrementally and are unaffected.
            nonlocal pending_tail
            if pending_tail:
                del thread[len(thread) - pending_tail:]
                pending_tail = 0

        try:
            while True:
                now_mono = time.monotonic()
                if now_mono >= deadline:
                    logger.info(
                        "[LIVE] %s — ending session.",
                        "Silence window expired" if engaged else "No response within grace window",
                    )
                    break
                if (now_mono - session_start) >= policy.live_session_max_wall_min * 60.0:
                    logger.info("[LIVE] Session wall-clock cap reached — ending session.")
                    break

                time.sleep(policy.live_session_poll_sec)

                # Presence-based early end: they were engaged but left LinkedIn.
                polls_since_presence_check += 1
                if engaged and polls_since_presence_check >= 6:
                    polls_since_presence_check = 0
                    if (
                        not _prospect_appears_online(page)
                        and (time.monotonic() - last_activity) > 60.0
                    ):
                        logger.info("[LIVE] Prospect went offline and quiet — ending session early.")
                        break

                # Cheap change detector before running full bubble extraction
                try:
                    total_now = page.locator(_MSG_BUBBLE_SEL).count()
                except Exception:
                    logger.warning("[LIVE] Lost the thread page — ending session.")
                    break
                if total_now == baseline_total:
                    continue
                baseline_total = total_now

                live_bubbles = self._scraper._extract_bubbles(page)

                # Our own bubble is what usually caused this DOM change. Correct
                # the scraper's guesses before counting, or we answer ourselves.
                _relabel_our_own_bubbles(live_bubbles, _our_text_set(thread))

                live_their = [m for m in live_bubbles if m.get("role") == "them"]
                if len(live_their) <= their_count:
                    continue  # DOM changed but no new inbound message (e.g. our bubble rendered late)

                # Hard invariant: never send two replies in a row. If the newest
                # bubble in the thread is ours, the prospect has not answered yet
                # and there is nothing to reply to — whatever grew the count was
                # not a genuine inbound. Anything genuinely unanswered is picked
                # up by the next sweep rather than risking a self-reply loop.
                if live_bubbles and live_bubbles[-1].get("role") == "us":
                    logger.info(
                        "[LIVE] Newest message in the thread is ours — waiting for a real reply."
                    )
                    continue

                new_msgs = live_their[their_count:]

                # Independent echo net (belt-and-suspenders to the relabel above):
                # even if a bubble slipped through mislabeled, never answer our own
                # message. This is the guard that stops the "3 near-identical
                # replies in a row" loop when LinkedIn's rendering drifts from what
                # we typed. If nothing genuinely new remains, wait for a real reply.
                our_fps_now = _our_fingerprints(thread)
                genuine = [m for m in new_msgs if not _is_our_message(m.get("text"), our_fps_now)]
                if not genuine:
                    logger.info(
                        "[LIVE] New bubble(s) are echoes of our own message(s) — waiting for a real reply."
                    )
                    their_count = len(live_their)  # don't re-scan the same echoes
                    continue
                new_msgs = genuine
                their_count = len(live_their)
                now_iso = datetime.now(timezone.utc).isoformat()
                for m in new_msgs:
                    m.setdefault("ts", now_iso)
                thread.extend(new_msgs)
                pending_tail = len(new_msgs)
                engaged = True
                last_activity = time.monotonic()
                latest_text = new_msgs[-1]["text"]
                logger.info("[LIVE] Prospect replied live (%d new): %s…", len(new_msgs), latest_text[:60])

                # Human read buffer — a touch longer for longer messages
                read_s = random.uniform(policy.live_read_buffer_min_sec, policy.live_read_buffer_max_sec)
                read_s += min(6.0, len(latest_text) / 80.0)
                logger.info("[LIVE] Reading for %.1fs before replying.", read_s)
                time.sleep(read_s)

                label, reason = self._classifier.classify_new_messages(thread, new_msgs)
                logger.info(
                    "[LIVE] Classified %d new message(s) as %s (%s).", len(new_msgs), label, reason
                )

                if label == "INTERESTED" and not interested_flag:
                    interested_flag = True
                    email_ok = self._send_live_handoff_email(
                        conv, prospect, profile_url, company_name, thread, latest_text,
                        f"live_session_interested:{reason}",
                    )

                if label == "NOT_INTERESTED":
                    declined_flag = True  # never close mid-chat; finalized after the session

                if session_replies >= policy.live_session_max_messages:
                    logger.info("[LIVE] Session reply cap reached — leaving last inbound for the next sweep.")
                    _trim_pending()
                    break

                if interested_flag:
                    gen_label = "LIVE_DISCOVERY"
                elif declined_flag:
                    gen_label = "LIVE_POLITE"
                else:
                    gen_label = label

                reply_text = self._generate_quality_reply(
                    thread=thread, prospect=prospect, company_name=company_name, label=gen_label,
                )
                if reply_text is None:
                    logger.warning("[LIVE] Quality gate produced nothing sendable — leaving last inbound for the next sweep.")
                    _trim_pending()
                    break

                # Live carve-out: no 45s spacing wait mid-conversation; the lock
                # still serialises typing so the account never types in two
                # threads at the same moment.
                with _GLOBAL_SEND_LOCK:
                    self._sender.send(reply_text, policy=self._policy)

                thread.append({
                    "role": "us",
                    "text": reply_text,
                    "ts":   datetime.now(timezone.utc).isoformat(),
                    "live": True,
                })
                inbound_count = pending_tail
                pending_tail = 0  # reply is on the wire — this exchange is now real
                session_replies += 1
                self._persist_live_exchange(conv, thread, inbound_count, sent_reply=True)

                # Fresh silence window after our reply
                deadline = time.monotonic() + policy.live_session_silence_end_min * 60.0
                last_activity = time.monotonic()
        finally:
            # Runs on EVERY exit — normal end, caps, or an exception mid-session —
            # so a conversation can never wedge in a half-live state and an
            # unanswered inbound is always left for the next sweep to retry.
            _trim_pending()
            if engaged:
                try:
                    self._finalize_live_session(conv, thread, interested_flag, declined_flag, email_ok)
                except Exception:
                    logger.exception("[LIVE] Failed to finalize session for prospect %d.", conv.prospect_id)
                with self._live_stats_lock:
                    self._live_stats["sessions"] += 1
                    self._live_stats["replies"]  += session_replies

        logger.info(
            "[LIVE] Session over for prospect %d: engaged=%s, live_replies=%d, interested=%s, declined=%s.",
            conv.prospect_id, engaged, session_replies, interested_flag, declined_flag,
        )
        return session_replies if engaged else 0

    def _send_live_handoff_email(
        self,
        conv: LinkedInConversationORM,
        prospect: ProspectORM,
        profile_url: str,
        company_name: str,
        thread: list[dict],
        latest_text: str,
        reason: str,
    ) -> bool:
        """
        Fire the LIVE-flagged manager email mid-session — at most once per
        conversation, EVER. The once-only guard reads the persisted DB flag
        (not just session-local state) so a session that crashed after emailing
        cannot re-email on the next INTERESTED classification, and the flag is
        persisted the moment the send succeeds so a crash right after this
        point is safe too. Never interrupts the chat.
        """
        try:
            with session_scope() as session:
                row = session.query(LinkedInConversationORM).filter_by(id=conv.id).one_or_none()
                if row is not None and row.handoff_email_sent:
                    logger.info(
                        "[LIVE] Handoff email already sent for prospect %d — skipping duplicate.",
                        conv.prospect_id,
                    )
                    return True
        except Exception:
            logger.warning("[LIVE] Could not read handoff-email flag — proceeding with send.", exc_info=True)

        ok, err = _send_handoff_email(
            self._cfg,
            prospect=prospect,
            profile_url=profile_url,
            company_name=company_name,
            thread=thread,
            latest_message=latest_text,
            reason=reason,
            live=True,
        )
        if err:
            logger.error("[LIVE] Handoff email failed (session continues regardless): %s", err)
            return False

        logger.info("[LIVE] Manager notified — prospect is live and interested.")
        # Persist immediately (not just at finalize) so a process death
        # mid-session can never lead to a duplicate manager email.
        try:
            with session_scope() as session:
                row = session.query(LinkedInConversationORM).filter_by(id=conv.id).one_or_none()
                if row is not None:
                    row.handoff_email_sent = True
        except Exception:
            logger.warning(
                "[LIVE] Email sent but flag persist failed — worst case is one duplicate email after a crash.",
                exc_info=True,
            )
        return ok

    def _persist_live_exchange(
        self,
        conv: LinkedInConversationORM,
        thread: list[dict],
        inbound_count: int,
        *,
        sent_reply: bool,
    ) -> None:
        """
        Persist one live exchange incrementally so the dashboard transcript grows
        in real time and a crash mid-session loses at most one exchange.
        bot_reply_count is deliberately NOT incremented — that counter drives the
        batch-mode escalation caps, and live sessions have their own caps.
        """
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            row = session.query(LinkedInConversationORM).filter_by(id=conv.id).one()
            row.thread_json             = json.dumps(thread)
            row.messages_received       = (row.messages_received or 0) + inbound_count
            row.last_reply_received_utc = now
            row.last_checked_utc        = now
            row.conversation_mode       = "live"
            row.last_live_detected_utc  = now
            if sent_reply:
                row.messages_sent         = (row.messages_sent or 0) + 1
                row.last_message_sent_utc = now

            total_their = row.messages_received
            if total_their >= 1 and row.lead_stage == "cold":
                row.lead_stage = "warming"
            if total_their >= 2 and row.lead_stage == "warming":
                row.lead_stage = "interested"

    def _finalize_live_session(
        self,
        conv: LinkedInConversationORM,
        thread: list[dict],
        interested_flag: bool,
        declined_flag: bool,
        email_ok: bool,
    ) -> None:
        """
        Apply the deferred outcome once the prospect has left:
        interested → handed_off (email already went out mid-session),
        declined   → not_interested/dead,
        otherwise  → stays active on normal scheduler cadence.
        """
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            row = session.query(LinkedInConversationORM).filter_by(id=conv.id).one()
            row.thread_json      = json.dumps(thread)
            row.last_checked_utc = now
            row.conversation_mode = "batch"
            if interested_flag:
                row.conversation_status = "handed_off"
                row.handed_off_at_utc   = now
                row.handoff_reason      = "live_session_interested"[:255]
                # Never CLEAR a flag another writer already persisted (the email
                # helper sets it the moment the send succeeds — audit fix C).
                row.handoff_email_sent  = bool(email_ok) or bool(row.handoff_email_sent)
                row.lead_stage          = "hot"
                if not row.handoff_email_sent:
                    row.last_error  = "handoff_email_failed during live session"[:2000]
                    row.error_count = (row.error_count or 0) + 1
            elif declined_flag:
                row.conversation_status = "not_interested"
                row.lead_stage          = "dead"
                row.handoff_reason      = "live_session_declined"[:255]
        logger.info(
            "[LIVE] Finalized session for prospect %d (interested=%s, declined=%s).",
            conv.prospect_id, interested_flag, declined_flag,
        )

    # ------------------------------------------------------------------
    # Handoff decision
    # ------------------------------------------------------------------

    def _decide_handoff(self, conv: LinkedInConversationORM, label: str, reason: str) -> Optional[str]:
        """Return a human-readable handoff reason string, or None to keep going."""
        if conv.bot_reply_count >= self._policy.max_bot_replies:
            return f"bot_reply_cap_reached({conv.bot_reply_count}/{self._policy.max_bot_replies})"
        if label == "INTERESTED":
            return f"classified_interested:{reason}"
        if label == "LIGHT_QUESTION" and conv.answered_question_count >= self._policy.max_light_questions:
            return (
                f"light_question_cap_reached({conv.answered_question_count}/"
                f"{self._policy.max_light_questions})"
            )
        return None

    def _handoff(
        self,
        conv: LinkedInConversationORM,
        prospect: ProspectORM,
        profile_url: str,
        company_name: str,
        updated_thread: list[dict],
        latest_text: str,
        reason: str,
        now: datetime,
    ) -> None:
        """
        Silently hand off: the bot sends nothing further. Persists the inbound
        message(s), flips conversation_status so the poller stops touching this
        row, and best-effort emails the manager (failure is logged, not fatal,
        and never sent twice).
        """
        already_emailed = bool(conv.handoff_email_sent)
        email_sent = already_emailed
        email_error: Optional[str] = None

        if not already_emailed:
            email_sent, email_error = _send_handoff_email(
                self._cfg,
                prospect=prospect,
                profile_url=profile_url,
                company_name=company_name,
                thread=updated_thread,
                latest_message=latest_text,
                reason=reason,
            )

        with session_scope() as session:
            row = session.query(LinkedInConversationORM).filter_by(id=conv.id).one()
            row.thread_json            = json.dumps(updated_thread)
            row.messages_received      = (row.messages_received or 0) + 1
            row.last_reply_received_utc = now
            row.last_checked_utc        = now
            row.conversation_status     = "handed_off"
            row.handed_off_at_utc       = now
            row.handoff_reason          = reason[:255]
            row.handoff_email_sent      = bool(email_sent)
            row.lead_stage              = "hot" if "interested" in reason.lower() else row.lead_stage
            if email_error:
                row.last_error  = f"handoff_email_failed: {email_error}"[:2000]
                row.error_count = (row.error_count or 0) + 1
            elif email_sent:
                row.last_error = None

        if email_error:
            logger.error(
                "[HANDOFF] Prospect %d handed off (%s) but manager email FAILED: %s",
                conv.prospect_id, reason, email_error,
            )
        else:
            logger.info("[HANDOFF] Prospect %d handed off (%s). Manager notified.", conv.prospect_id, reason)

    # ------------------------------------------------------------------
    # Safety caps
    # ------------------------------------------------------------------

    def _check_safety_caps(self, conv: LinkedInConversationORM, now: datetime) -> Optional[str]:
        cutoff = now - timedelta(hours=24)
        recent = conv.count_our_messages_since(cutoff)
        if recent >= self._policy.max_replies_per_conversation_24h:
            return f"per_conversation_24h_cap_reached({recent}/{self._policy.max_replies_per_conversation_24h})"

        gap = self._seconds_since_last_global_send(now)
        if gap is not None and gap < self._policy.min_global_spacing_sec:
            return f"global_min_spacing_not_met({gap:.1f}s<{self._policy.min_global_spacing_sec}s)"

        return None

    def _seconds_since_last_global_send(self, now: datetime) -> Optional[float]:
        with session_scope() as session:
            last_sent = session.query(func.max(LinkedInConversationORM.last_message_sent_utc)).scalar()
        last_sent = _aware(last_sent)
        if last_sent is None:
            return None
        return (now - last_sent).total_seconds()

    # ------------------------------------------------------------------
    # Live/batch detection
    # ------------------------------------------------------------------

    def _detect_live(self, conv: LinkedInConversationORM, latest_msg: dict, now: datetime) -> bool:
        """
        LIVE if the gap between the prospect's reply and our last outbound message
        is within live_window_minutes. Prefers a real scraped timestamp on the
        message; falls back to "now" (i.e. wall-clock-since-our-last-send) when no
        reliable timestamp could be extracted from the DOM.
        """
        last_sent = _aware(conv.last_message_sent_utc)
        if last_sent is None:
            return False

        reply_ts = _parse_ts(latest_msg.get("ts")) or now
        gap_minutes = (reply_ts - last_sent).total_seconds() / 60.0
        return 0 <= gap_minutes <= self._policy.live_window_minutes

    def _maybe_revert_live_mode(self, conv: LinkedInConversationORM, now: datetime) -> None:
        """If a 'live' conversation has gone quiet past live_revert_minutes, drop it back to batch."""
        if conv.conversation_mode != "live":
            return
        last_live = _aware(conv.last_live_detected_utc)
        if last_live is None:
            return
        silence_minutes = (now - last_live).total_seconds() / 60.0
        if silence_minutes > self._policy.live_revert_minutes:
            conv.conversation_mode = "batch"
            with session_scope() as session:
                row = session.query(LinkedInConversationORM).filter_by(id=conv.id).one_or_none()
                if row:
                    row.conversation_mode = "batch"
            logger.info(
                "[PROCESS] Conversation %d reverted to batch mode after %.1f min of silence.",
                conv.id, silence_minutes,
            )

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
            # Stamp a fallback timestamp only where the scraper couldn't find a real one.
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
        Re-open the message thread so the compose box is available for sending.
        Mirrors scrape_thread's strategy: direct URL first (most reliable),
        then JS click, then normal click.  After each attempt we verify the
        compose area is actually present — not just that it isn't a promo page —
        so a silent click failure cannot mask the real state.
        """
        page = self._browser.page
        compose_sel = ", ".join(_MSG_COMPOSE_SELS)

        def _compose_ready(timeout_ms: int = 4000) -> bool:
            try:
                page.wait_for_selector(compose_sel, state="visible", timeout=timeout_ms)
                return True
            except Exception:
                return False

        # ── Navigate to profile and find the Message link ──────────────────
        logger.info("[REOPEN] Navigating to profile: %s", profile_url)
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
        except Exception as exc:
            raise RuntimeError(f"Could not navigate to {profile_url}: {exc}") from exc

        msg_link = self._scraper._first_visible(page, _MSG_LINK_SELS, timeout=6000)

        if msg_link is None:
            raise RuntimeError(
                f"Message link not found on {profile_url} when trying to reply."
            )

        href = ""
        try:
            href = msg_link.get_attribute("href") or ""
            if href.startswith("/"):
                href = "https://www.linkedin.com" + href
            logger.info("[REOPEN] Message link href: %s", href)
        except Exception:
            pass

        # Dismiss popups
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception:
            pass

        # ── Attempt 1: direct URL navigation (same path that worked in scraper) ──
        if href:
            try:
                logger.info("[REOPEN] Navigating directly to messaging URL: %s", href)
                page.goto(href, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1500)
            except Exception as exc:
                logger.warning("[REOPEN] Direct URL navigation failed: %s", exc)

            if _is_promo_page(page):
                logger.warning("[REOPEN] Direct URL → promo page (%s). Recovering.", page.url)
                _recover_from_promo(page, profile_url, logger)
            elif _compose_ready():
                logger.info("[REOPEN] Compose area ready after direct URL navigation.")
                return

        # ── Attempt 2: JS click (bypasses pointer-event interception) ──────
        try:
            msg_link.evaluate("el => el.click()")
            page.wait_for_timeout(1500)
            logger.info("[REOPEN] JS click fired.")
        except Exception as exc:
            logger.warning("[REOPEN] JS click failed: %s", exc)

        if _is_promo_page(page):
            logger.warning("[REOPEN] JS click → promo page. Recovering.")
            _recover_from_promo(page, profile_url, logger)
        elif _compose_ready():
            logger.info("[REOPEN] Compose area ready after JS click.")
            return

        # ── Attempt 3: normal click ──────────────────────────────────────
        try:
            msg_link.click(timeout=4000)
            page.wait_for_timeout(1500)
            logger.info("[REOPEN] Normal click fired.")
        except Exception as exc:
            logger.warning("[REOPEN] Normal click failed: %s", exc)

        if _is_promo_page(page):
            logger.warning("[REOPEN] Normal click → promo page. Recovering.")
            _recover_from_promo(page, profile_url, logger)
        elif _compose_ready():
            logger.info("[REOPEN] Compose area ready after normal click.")
            return

        # ── Final wait: LinkedIn sometimes renders the compose box late ───
        try:
            page.wait_for_selector(compose_sel, state="visible", timeout=10000)
            logger.info("[REOPEN] Compose area ready (late render).")
            return
        except PlaywrightTimeoutError:
            pass

        raise RuntimeError(
            "Compose area did not appear after opening messaging for reply."
        )

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
                # MySQL sorts NULLs first in ASC order by default (unlike Postgres),
                # so plain .asc() already gives never-checked conversations priority.
                # .nullsfirst() compiles to "NULLS FIRST", which MySQL does not support.
                .order_by(LinkedInConversationORM.last_checked_utc.asc())
                .all()
            )
            # Apply agent filter if provided
            if self._agent_id is not None:
                rows = [r for r in rows if r.agent_id == self._agent_id]
            for r in rows:
                session.expunge(r)
            return rows

    def _fetch_live_conversation_ids(self, conv_ids: List[int]) -> List[int]:
        if not conv_ids:
            return []
        with session_scope() as session:
            rows = (
                session.query(LinkedInConversationORM.id)
                .filter(
                    LinkedInConversationORM.id.in_(conv_ids),
                    LinkedInConversationORM.conversation_status == "active",
                    LinkedInConversationORM.conversation_mode == "live",
                )
                .all()
            )
            return [r[0] for r in rows]

    def _load_conversation(self, conv_id: int) -> Optional[LinkedInConversationORM]:
        with session_scope() as session:
            row = session.query(LinkedInConversationORM).filter_by(id=conv_id).one_or_none()
            if row:
                session.expunge(row)
            return row

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
        label:          str,
        is_live:        bool,
        now:            datetime,
    ) -> None:
        with session_scope() as session:
            row = session.query(LinkedInConversationORM).filter_by(id=conv.id).one()
            row.thread_json             = json.dumps(updated_thread)
            row.messages_received       = (row.messages_received or 0) + len(new_their_msgs)
            row.messages_sent           = (row.messages_sent or 0) + 1
            row.bot_reply_count         = (row.bot_reply_count or 0) + 1
            if label == "LIGHT_QUESTION":
                row.answered_question_count = (row.answered_question_count or 0) + 1
            row.last_reply_received_utc = now
            row.last_message_sent_utc   = now
            row.last_checked_utc        = now
            row.last_error              = None  # clear previous errors on success
            if is_live:
                row.conversation_mode      = "live"
                row.last_live_detected_utc = now

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
