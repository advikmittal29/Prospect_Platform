"""
Scheduler entry point: LinkedIn Reply Handler
=============================================
Polls LinkedIn inbox for replies from prospects who have already received
our first outreach message, generates AI replies via Gemini, and sends them.

Usage
-----
Normal run (check all active conversations once):
    python scheduler/p_reply_handler.py
    python scheduler/p_reply_handler.py --agent-id 1

Single-prospect test (reads DB conversation, scrapes LinkedIn, sends AI reply):
    python scheduler/p_reply_handler.py --test-prospect-id 42

Create a conversation row manually (after sending first message outside the app):
    python scheduler/p_reply_handler.py --register-prospect-id 42

Schedule
--------
Run this script every 10-15 minutes via Windows Task Scheduler or a cron job.
It is safe to run while p_outreach.py is also running — they use different DB tables.

Prerequisites
-------------
- Chrome must be open and logged into LinkedIn (same as p_outreach.py).
- MY_LINKEDIN_NAME must be set in .env (your LinkedIn display name, used to
  classify messages as "us" vs "them").
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents import AgentConfigResolver, AgentInactiveError, AgentRunTracker
from config import AppConfig, DatabaseConfig, reset_runtime_settings_cache
from db import init_db
from outreach.linkedin_reply_handler import LinkedInReplyHandler
from research.linkedin_browser import ChromeLauncher, LinkedInBrowser
from utils.logging import build_logger

logger = build_logger("prospect.scheduler.reply_handler", level=logging.INFO)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll LinkedIn inbox and send AI-generated replies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Normal production run — check all active conversations
  python scheduler/p_reply_handler.py

  # Limit to a specific agent
  python scheduler/p_reply_handler.py --agent-id 1

  # Test with one specific prospect (reads their conversation from DB)
  python scheduler/p_reply_handler.py --test-prospect-id 42

  # Register a prospect's first message manually (after sending outside the app)
  python scheduler/p_reply_handler.py --register-prospect-id 42 --first-message "Hi, I reached out..."
        """,
    )
    parser.add_argument("--agent-id",  type=int, default=None)
    parser.add_argument("--agent-key", type=str, default=None)

    parser.add_argument(
        "--test-prospect-id",
        type=int,
        default=None,
        metavar="PROSPECT_ID",
        help="Run the reply check for one specific prospect only (reads from DB).",
    )
    parser.add_argument(
        "--register-prospect-id",
        type=int,
        default=None,
        metavar="PROSPECT_ID",
        help="Create a conversation row for this prospect (use after manual first send).",
    )
    parser.add_argument(
        "--first-message",
        type=str,
        default="[First outreach message — update this text]",
        help="First message text to store when using --register-prospect-id.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_prospect(args: argparse.Namespace) -> int:
    """Create a linkedin_conversations row for a prospect given by --register-prospect-id."""
    from db import ProspectORM, session_scope

    pid = args.register_prospect_id
    with session_scope() as session:
        prospect = session.query(ProspectORM).filter_by(id=pid).one_or_none()
        if prospect is None:
            logger.error("Prospect %d not found in DB.", pid)
            return 1
        url = prospect.linkedin_profile_url
        agent_id = prospect.agent_id

    LinkedInReplyHandler.create_conversation_for_prospect(
        prospect_id          = pid,
        agent_id             = agent_id,
        linkedin_profile_url = url,
        first_message_text   = args.first_message,
    )
    logger.info("Registered conversation for prospect %d (%s).", pid, url)
    return 0


def _test_single_prospect(
    args:    argparse.Namespace,
    config:  AppConfig,
    browser: LinkedInBrowser,
    agent_id: int,
) -> int:
    """Run the reply handler for one prospect only."""
    from db import session_scope
    from db.models import LinkedInConversationORM

    pid = args.test_prospect_id
    with session_scope() as session:
        conv = session.query(LinkedInConversationORM).filter_by(
            prospect_id=pid
        ).one_or_none()
        if conv is None:
            logger.error(
                "No linkedin_conversations row found for prospect %d. "
                "Run --register-prospect-id %d first.",
                pid, pid,
            )
            return 1
        session.expunge(conv)

    handler = LinkedInReplyHandler(browser=browser, config=config, agent_id=agent_id)
    result  = handler._process_one(conv)   # noqa: SLF001  (internal test access)
    logger.info("Test result for prospect %d: %s", pid, result)
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args     = _parse_args()
    register = bool(args.register_prospect_id)
    test     = bool(args.test_prospect_id)

    logger.info("=" * 60)
    logger.info(
        "REPLY HANDLER  %s",
        "REGISTER" if register else "TEST" if test else "START",
    )
    logger.info("=" * 60)

    browser: LinkedInBrowser | None = None

    try:
        init_db(DatabaseConfig())
        reset_runtime_settings_cache()
        config = AppConfig()

        # --register-prospect-id doesn't need a browser
        if register:
            return _register_prospect(args)

        # Everything else needs Chrome + LinkedIn session
        launcher = ChromeLauncher(config.chrome)
        launcher.launch_if_needed()

        browser = LinkedInBrowser(config.chrome)
        browser.start()
        browser.ensure_logged_in()

        agent = AgentConfigResolver(config).resolve(
            agent_id  = args.agent_id,
            agent_key = args.agent_key,
        )

        if test:
            return _test_single_prospect(args, config, browser, agent.agent_id)

        # ── Normal production run ──────────────────────────────────────
        with AgentRunTracker(
            agent_id   = agent.agent_id,
            pipeline   = "linkedin_reply_handler",
            run_config = {"agent_key": agent.agent_key},
        ) as tracker:
            handler = LinkedInReplyHandler(
                browser  = browser,
                config   = config,
                agent_id = agent.agent_id,
            )
            stats = handler.run()
            tracker.mark_completed(metrics=stats)

        logger.info("REPLY HANDLER COMPLETE. Stats:")
        for k, v in stats.items():
            logger.info("  %-40s  %s", k, v)

        return 0

    except AgentInactiveError as exc:
        logger.warning("REPLY HANDLER SKIPPED — %s", exc)
        return 0
    except Exception:
        logger.error("REPLY HANDLER FAILED\n%s", traceback.format_exc())
        return 1
    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                logger.warning("Browser stop raised an exception.", exc_info=True)


if __name__ == "__main__":
    sys.exit(main())