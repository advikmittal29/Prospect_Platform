"""
Scheduler entry point: LinkedIn Outreach Sender

Normal production run (via Windows Task Scheduler or manually):
    python scheduler/p_outreach.py
    python scheduler/p_outreach.py --agent-id 1
    python scheduler/p_outreach.py --agent-key my-agent

CLI test mode (single profile, no DB read, no DB write):
    python scheduler/p_outreach.py --url "https://www.linkedin.com/in/someone/" --message "Hi there!"
    python scheduler/p_outreach.py --url "https://www.linkedin.com/in/someone/" --action connect
    python scheduler/p_outreach.py --url "https://www.linkedin.com/in/someone/" --action auto

Test-mode flags:
    --url       LinkedIn profile URL to target  (required for test mode)
    --message   Message text to send / use as connection note
    --action    Force a specific action: connect | message | auto (default: auto)
                auto = detect connection state and act accordingly

Notes:
  - Test mode bypasses the DB entirely (no outreach_required lookup, no state writes).
  - Auth-wall handling and CDP browser attachment work identically to production.
  - All normal prerequisites still apply (Chrome must be open and logged in).

Prerequisites:
  - Chrome must be open and logged into LinkedIn, OR Chrome CDP will be launched
    automatically using the configured CHROME_EXE and CHROME_USER_DATA_DIR.
  - The user profile in CHROME_USER_DATA_DIR must already be logged into LinkedIn.

All outreach logic lives in outreach.linkedin_outreach_sender.LinkedInOutreachSender.
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
from outreach.linkedin_outreach_sender import LinkedInOutreachSender
from research.linkedin_browser import ChromeLauncher, LinkedInBrowser
from utils.logging import build_logger

logger = build_logger("prospect.scheduler.outreach", level=logging.INFO)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LinkedIn outreach pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Normal production run
  python scheduler/p_outreach.py

  # Test a single profile (auto-detect state, use message as connect note if not connected)
  python scheduler/p_outreach.py --url "https://www.linkedin.com/in/someone/" --message "Hi!"

  # Force connect with a note
  python scheduler/p_outreach.py --url "https://www.linkedin.com/in/someone/" --action connect --message "Hi!"

  # Force message send (profile must be connected)
  python scheduler/p_outreach.py --url "https://www.linkedin.com/in/someone/" --action message --message "Hi!"
        """,
    )
    # Production args
    parser.add_argument("--agent-id",  type=int, default=None, help="Target agent id.")
    parser.add_argument("--agent-key", type=str, default=None, help="Target agent key.")

    # Test-mode args
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        metavar="LINKEDIN_URL",
        help="[TEST MODE] LinkedIn profile URL to target. Bypasses DB fetch.",
    )
    parser.add_argument(
        "--message",
        type=str,
        default=None,
        metavar="TEXT",
        help="[TEST MODE] Message text to send or use as connection note.",
    )
    parser.add_argument(
        "--action",
        type=str,
        default="auto",
        choices=["auto", "connect", "message"],
        help=(
            "[TEST MODE] Force a specific action. "
            "'auto' detects connection state (default). "
            "'connect' forces a connect request. "
            "'message' forces a direct message."
        ),
    )
    return parser.parse_args()


def _is_test_mode(args: argparse.Namespace) -> bool:
    return bool(args.url)


# ---------------------------------------------------------------------------
# Production run
# ---------------------------------------------------------------------------

def _run_production(args: argparse.Namespace, config: AppConfig, browser: LinkedInBrowser) -> int:
    from agents.guard import require_agent_active
    agent = AgentConfigResolver(config).resolve(
        agent_id=args.agent_id,
        agent_key=args.agent_key,
    )
    require_agent_active(agent.agent_id, pipeline="linkedin_outreach")

    if agent.pipeline_policy.get("linkedin_outreach_enabled") is False:
        logger.info(
            "LinkedIn outreach skipped: agent '%s' has linkedin_outreach_enabled=false",
            agent.agent_key,
        )
        return 0

    with AgentRunTracker(
        agent_id=agent.agent_id,
        pipeline="linkedin_outreach",
        run_config={"agent_key": agent.agent_key},
    ) as tracker:
        sender = LinkedInOutreachSender(
            browser=browser,
            config=config,
            agent_id=agent.agent_id,
        )
        stats = sender.run()
        tracker.mark_completed(metrics=stats)

    logger.info("LinkedIn outreach finished. Stats:")
    for key, val in stats.items():
        logger.info("  %-40s  %s", key, val)

    return 0


# ---------------------------------------------------------------------------
# Test run (single profile, no DB)
# ---------------------------------------------------------------------------

def _run_test(args: argparse.Namespace, config: AppConfig, browser: LinkedInBrowser) -> int:
    logger.info("─" * 60)
    logger.info("TEST MODE  –  single profile, no DB reads/writes")
    logger.info("  URL    : %s", args.url)
    logger.info("  action : %s", args.action)
    logger.info("  message: %s", args.message or "(none)")
    logger.info("─" * 60)

    sender = LinkedInOutreachSender(browser=browser, config=config)
    result = sender.run_test(
        url=args.url,
        message=args.message,
        force_action=args.action,   # "auto" | "connect" | "message"
    )

    logger.info("─" * 60)
    logger.info("TEST RESULT")
    logger.info("  action  : %s", result.action.value)
    logger.info("  success : %s", result.success)
    logger.info("  error   : %s", result.error or "—")
    logger.info("─" * 60)

    return 0 if result.success else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    args = _parse_args()
    test_mode = _is_test_mode(args)

    logger.info("=" * 60)
    logger.info("LINKEDIN OUTREACH  %s", "TEST START" if test_mode else "START")
    logger.info("=" * 60)

    browser: LinkedInBrowser | None = None

    try:
        init_db(DatabaseConfig())
        reset_runtime_settings_cache()
        config = AppConfig()

        launcher = ChromeLauncher(config.chrome)
        launcher.launch_if_needed()

        browser = LinkedInBrowser(config.chrome)
        browser.start()
        browser.ensure_logged_in()

        if test_mode:
            rc = _run_test(args, config, browser)
        else:
            rc = _run_production(args, config, browser)

        logger.info("LINKEDIN OUTREACH  %s", "TEST COMPLETE" if test_mode else "COMPLETE")
        return rc

    except AgentInactiveError as exc:
        logger.warning("LINKEDIN OUTREACH SKIPPED — %s", exc)
        return 0
    except Exception:
        logger.error("LINKEDIN OUTREACH  FAILED\n%s", traceback.format_exc())
        return 1

    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                logger.warning("Browser stop raised an exception.", exc_info=True)


if __name__ == "__main__":
    sys.exit(main())
