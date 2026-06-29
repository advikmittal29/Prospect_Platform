"""
Scheduler entry point: LinkedIn Candidate Hunt

Run via Windows Task Scheduler (or manually):
    python scheduler/run_candidate_hunt.py
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
from candidate_hunt.pipeline import CandidateHuntPipeline
from config import AppConfig, DatabaseConfig, reset_runtime_settings_cache
from db import init_db
from utils.logging import build_logger

logger = build_logger("prospect.scheduler.candidate_hunt", level=logging.INFO)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run candidate hunt pipeline.")
    parser.add_argument("--agent-id", type=int, default=None, help="Target agent id.")
    parser.add_argument("--agent-key", type=str, default=None, help="Target agent key.")
    parser.add_argument(
        "--runtime-mode",
        type=str,
        choices=["deterministic", "autonomous"],
        default=None,
        help="Override runtime mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logger.info("=" * 60)
    logger.info("CANDIDATE HUNT PIPELINE START")
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
        require_agent_active(agent.agent_id, pipeline="candidate_hunt")

        if agent.pipeline_policy.get("candidate_hunt_enabled") is False:
            logger.info("Candidate hunt skipped: agent '%s' has candidate_hunt_enabled=false", agent.agent_key)
            return 0

        with AgentRunTracker(
            agent_id=agent.agent_id,
            pipeline="candidate_hunt",
            run_config={
                "agent_key": agent.agent_key,
                "runtime_mode": args.runtime_mode or agent.runtime_mode,
            },
        ) as tracker:
            pipeline = CandidateHuntPipeline(
                config,
                agent_id=agent.agent_id,
                agent_context=agent.prompt_context,
                runtime_mode=args.runtime_mode or agent.runtime_mode,
            )
            stats = pipeline.run()
            tracker.mark_completed(metrics=stats)

        logger.info("Candidate hunt finished. Stats:")
        for key, val in stats.items():
            logger.info("  %-40s  %s", key, val)

        logger.info("CANDIDATE HUNT PIPELINE COMPLETE")
        return 0

    except Exception:
        logger.error("CANDIDATE HUNT PIPELINE FAILED\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
