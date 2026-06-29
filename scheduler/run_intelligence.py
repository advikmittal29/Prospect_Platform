"""
Scheduler entry point: Intelligence (Dossiers) + Outreach Messages

Run via Windows Task Scheduler (or manually):
    python scheduler/run_intelligence.py

Prerequisites:
- LLM_API_KEY must be set (OpenAI key).
- Prospects must have been assessed (run_research.py must have run first).
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
from intelligence.dossier_generator import DossierGenerator
from outreach.message_generator import OutreachGenerator
from utils.logging import build_logger

logger = build_logger("prospect.scheduler.intelligence", level=logging.INFO)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run intelligence + outreach pipeline.")
    parser.add_argument("--agent-id", type=int, default=None, help="Target agent id.")
    parser.add_argument("--agent-key", type=str, default=None, help="Target agent key.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logger.info("=" * 60)
    logger.info("INTELLIGENCE + OUTREACH  START")
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
        require_agent_active(agent.agent_id, pipeline="intelligence")
        
        intelligence_enabled = agent.pipeline_policy.get("intelligence_enabled", True) is not False
        outreach_enabled = agent.pipeline_policy.get("outreach_enabled", True) is not False
        if not intelligence_enabled and not outreach_enabled:
            logger.info(
                "Intelligence+Outreach skipped: agent '%s' has both intelligence and outreach disabled",
                agent.agent_key,
            )
            return 0

        with AgentRunTracker(
            agent_id=agent.agent_id,
            pipeline="intelligence",
            run_config={"agent_key": agent.agent_key},
        ) as tracker:
            # --- Phase 1: Generate dossiers ---
            if intelligence_enabled:
                logger.info("Phase 1: Generating prospect dossiers...")
                dossier_gen = DossierGenerator(
                    config,
                    agent_id=agent.agent_id,
                    agent_context=agent.prompt_context,
                )
                dossier_stats = dossier_gen.run()
                logger.info("Dossier stats: %s", dossier_stats)
            else:
                dossier_stats = {"skipped": 1}
                logger.info("Phase 1 skipped: intelligence_enabled=false")

            # --- Phase 2: Generate outreach messages ---
            if outreach_enabled:
                logger.info("Phase 2: Generating outreach messages")
                outreach_gen = OutreachGenerator(
                    config,
                    agent_id=agent.agent_id,
                    agent_context=agent.prompt_context,
                )
                outreach_stats = outreach_gen.run(
                    recruiter_name=config.outreach.recruiter_name,
                    agency_name=config.outreach.agency_name,
                )
                logger.info("Outreach stats: %s", outreach_stats)
            else:
                outreach_stats = {"skipped": 1}
                logger.info("Phase 2 skipped: outreach_enabled=false")
            tracker.mark_completed(metrics={"dossier": dossier_stats, "outreach": outreach_stats})

        logger.info("INTELLIGENCE + OUTREACH  COMPLETE")
        return 0

    except AgentInactiveError as exc:
        logger.warning("INTELLIGENCE PIPELINE SKIPPED — %s", exc)
        return 0
    except Exception:
        logger.error("INTELLIGENCE PIPELINE  FAILED\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
