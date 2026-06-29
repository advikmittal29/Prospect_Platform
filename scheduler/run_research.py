"""
Scheduler entry point: Company + People Research

Run via Windows Task Scheduler (or manually):
    python scheduler/run_research.py

Prerequisites:
- Chrome must be open and logged into LinkedIn, OR Chrome CDP will be launched
  automatically using the configured CHROME_EXE and CHROME_USER_DATA_DIR.
- The user profile in CHROME_USER_DATA_DIR must already be logged into LinkedIn.
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

from agents import AgentConfigResolver, AgentRunTracker
from agents.guard import AgentInactiveError
from config import AppConfig, DatabaseConfig, reset_runtime_settings_cache
from db import init_db
from research.pipeline import ResearchPipeline
from utils.logging import build_logger

logger = build_logger("prospect.scheduler.research", level=logging.INFO)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run research pipeline.")
    parser.add_argument("--agent-id", type=int, default=None, help="Target agent id.")
    parser.add_argument("--agent-key", type=str, default=None, help="Target agent key.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logger.info("=" * 60)
    logger.info("RESEARCH PIPELINE  START")
    logger.info("=" * 60)

    try:
        init_db(DatabaseConfig())
        reset_runtime_settings_cache()
        config = AppConfig()
        agent = AgentConfigResolver(config).resolve(
            agent_id=args.agent_id,
            agent_key=args.agent_key,
        )
        from agents.guard import require_agent_active
        require_agent_active(agent.agent_id, pipeline="research")
        
        if agent.pipeline_policy.get("research_enabled") is False:
            logger.info("Research skipped: agent '%s' has research_enabled=false", agent.agent_key)
            return 0

        with AgentRunTracker(
            agent_id=agent.agent_id,
            pipeline="research",
            run_config={"agent_key": agent.agent_key},
        ) as tracker:
            pipeline = ResearchPipeline(
                config,
                agent_id=agent.agent_id,
                agent_context=agent.prompt_context,
                prospect_keywords=agent.keywords_by_type.get("people_search"),
            )
            stats = pipeline.run()
            tracker.mark_completed(metrics=stats)

        logger.info("Research finished. Stats:")
        for key, val in stats.items():
            logger.info("  %-40s  %d", key, val)

        logger.info("RESEARCH PIPELINE  COMPLETE")
        return 0

    except AgentInactiveError as exc:
        logger.warning("RESEARCH PIPELINE SKIPPED — %s", exc)
        return 0
    except Exception:
        logger.error("RESEARCH PIPELINE  FAILED\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())




