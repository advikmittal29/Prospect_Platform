"""
Scheduler entry point: Job Ingestion

Run via Windows Task Scheduler (or manually):
    python scheduler/run_ingest.py
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

# Allow running as a script from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents import AgentConfigResolver, AgentRunTracker
from agents.guard import AgentInactiveError
from config import AppConfig, DatabaseConfig, reset_runtime_settings_cache
from db import init_db, seed_default_keywords
from jobs.naukri_ingest import NaukriIngestionRunner
from utils.logging import build_logger

logger = build_logger("prospect.scheduler.ingest", level=logging.INFO)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run job ingestion pipeline.")
    parser.add_argument("--agent-id", type=int, default=None, help="Target agent id.")
    parser.add_argument("--agent-key", type=str, default=None, help="Target agent key.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logger.info("=" * 60)
    logger.info("JOB INGESTION START")
    logger.info("=" * 60)

    try:
        init_db(DatabaseConfig())
        reset_runtime_settings_cache()
        config = AppConfig()
        print("DEFAULT AGENT =", config.agent_runtime.default_agent_key)
        agent = AgentConfigResolver(config).resolve(
            agent_id=args.agent_id,
            agent_key=args.agent_key,
        )
        from agents.guard import require_agent_active
        require_agent_active(agent.agent_id, pipeline="ingest")
        
        if agent.pipeline_policy.get("ingest_enabled") is False:
            logger.info("Ingest skipped: agent '%s' has ingest_enabled=false", agent.agent_key)
            return 0
        seed_default_keywords(
            config.job_ingestion.keywords,
            config.job_ingestion.location,
        )

        with AgentRunTracker(
            agent_id=agent.agent_id,
            pipeline="ingest",
            run_config={"agent_key": agent.agent_key},
        ) as tracker:
            runner = NaukriIngestionRunner(config, agent_id=agent.agent_id)
            stats = runner.run()
            tracker.mark_completed(metrics=stats)

        logger.info("Ingestion finished. Stats:")
        for keyword, count in stats.items():
            logger.info("  %-40s  %d jobs saved", keyword, count)

        logger.info("JOB INGESTION COMPLETE")
        return 0

    except AgentInactiveError as exc:
        logger.warning("JOB INGESTION SKIPPED — %s", exc)
        return 0
    except Exception:
        logger.error("JOB INGESTION  FAILED\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())



