"""
Scheduler entry point: Website RAG Ingestion

Crawls the configured target website, chunks + embeds the content, and
upserts it into the ChromaDB knowledge base used by the LinkedIn reply
handler (see rag/retriever.py). Idempotent — re-running refreshes the
"website" source rather than duplicating it (see ChromaVectorStore.reset_source).

Progress (pages crawled, chunks embedded, % complete, ETA) is written
incrementally to the `ingestion_runs` table so the UI can poll it live.

Run via the app UI's "Run Ingestion" button (POST /api/runs/trigger with
pipeline=rag_ingest), Windows Task Scheduler, or manually:
    python scheduler/run_rag_ingest.py
    python scheduler/run_rag_ingest.py --url https://gnxtsystems.com --depth 3
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import text as sa_text

from config import AppConfig, DatabaseConfig, reset_runtime_settings_cache
from db import init_db, session_scope
from db.models import IngestionRunORM
from rag.ingest import ingest_website
from utils.logging import build_logger

logger = build_logger("prospect.scheduler.rag_ingest", level=logging.INFO)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run website RAG ingestion.")
    parser.add_argument("--url", type=str, default=None, help="Override the target URL (default: RAG_TARGET_URL setting).")
    parser.add_argument("--depth", type=int, default=3, help="Max crawl depth.")
    # Accepted for compatibility with the generic UI pipeline-trigger mechanism
    # (which always passes --agent-id/--agent-key). Ingestion is not agent-scoped.
    parser.add_argument("--agent-id", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--agent-key", type=str, default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def _progress_pct(stage: str, pages_crawled: int, pages_in_queue: int, chunks_embedded: int, chunks_created: int) -> float:
    if stage == "crawling":
        denom = max(1, pages_crawled + pages_in_queue)
        return min(50.0, 50.0 * pages_crawled / denom)
    if stage == "embedding" and chunks_created > 0:
        return 50.0 + min(50.0, 50.0 * chunks_embedded / chunks_created)
    return 50.0


def main() -> int:
    args = _parse_args()
    logger.info("=" * 60)
    logger.info("WEBSITE RAG INGESTION START")
    logger.info("=" * 60)

    try:
        init_db(DatabaseConfig())
        reset_runtime_settings_cache()
        config = AppConfig()

        target_url = args.url or config.rag.target_url

        with session_scope() as session:
            already_running = (
                session.query(IngestionRunORM)
                .filter(IngestionRunORM.status == "running")
                .first()
            )
            if already_running is not None:
                logger.warning(
                    "Refusing to start — ingestion run #%s is already in progress.",
                    already_running.id,
                )
                return 1

            run = IngestionRunORM(
                source="website",
                target_url=target_url,
                status="running",
                stage="crawling",
                triggered_by="scheduler",
            )
            session.add(run)
            session.flush()
            run_id = run.id

        started_wall = time.monotonic()

        def _on_progress(update: dict) -> None:
            stage = update.get("stage", "crawling")
            pages_crawled = update.get("pages_crawled", 0)
            pages_in_queue = update.get("pages_in_queue", 0)
            chunks_embedded = update.get("chunks_embedded", 0)
            chunks_created = update.get("chunks_created", 0)
            pct = _progress_pct(stage, pages_crawled, pages_in_queue, chunks_embedded, chunks_created)
            elapsed = max(0.001, time.monotonic() - started_wall)
            eta_seconds = int(elapsed * (100.0 - pct) / pct) if pct > 0 else None

            try:
                with session_scope() as session:
                    session.execute(
                        sa_text(
                            """
                            UPDATE ingestion_runs
                            SET stage = :stage,
                                pages_crawled = :pages_crawled,
                                pages_in_queue = :pages_in_queue,
                                chunks_created = :chunks_created,
                                chunks_embedded = :chunks_embedded,
                                progress_pct = :progress_pct,
                                eta_seconds = :eta_seconds
                            WHERE id = :id
                            """
                        ),
                        {
                            "stage": stage,
                            "pages_crawled": pages_crawled,
                            "pages_in_queue": pages_in_queue,
                            "chunks_created": chunks_created,
                            "chunks_embedded": chunks_embedded,
                            "progress_pct": pct,
                            "eta_seconds": eta_seconds,
                            "id": run_id,
                        },
                    )
            except Exception:
                # Progress reporting must never abort the ingestion itself.
                logger.warning("Failed to write ingestion progress (run #%d).", run_id, exc_info=True)

        try:
            stats = ingest_website(
                url=target_url,
                rag_cfg=config.rag,
                llm_cfg=config.llm,
                max_depth=args.depth,
                on_progress=_on_progress,
            )
        except Exception:
            error_text = traceback.format_exc()
            with session_scope() as session:
                session.execute(
                    sa_text(
                        """
                        UPDATE ingestion_runs
                        SET status = 'failed', error_text = :error_text, ended_at_utc = :ended_at
                        WHERE id = :id
                        """
                    ),
                    {"error_text": error_text[-8000:], "ended_at": datetime.now(timezone.utc), "id": run_id},
                )
            logger.error("WEBSITE RAG INGESTION FAILED\n%s", error_text)
            return 1

        with session_scope() as session:
            session.execute(
                sa_text(
                    """
                    UPDATE ingestion_runs
                    SET status = 'completed', stage = 'embedding', progress_pct = 100.0, eta_seconds = 0,
                        pages_crawled = :pages_crawled, chunks_created = :chunks_created,
                        chunks_embedded = :chunks_created, chunks_stored = :chunks_stored,
                        ended_at_utc = :ended_at
                    WHERE id = :id
                    """
                ),
                {
                    "pages_crawled": stats.pages_crawled,
                    "chunks_created": stats.chunks_created,
                    "chunks_stored": stats.chunks_stored,
                    "ended_at": datetime.now(timezone.utc),
                    "id": run_id,
                },
            )

        logger.info(
            "WEBSITE RAG INGESTION COMPLETE  pages=%d chunks_created=%d chunks_stored=%d",
            stats.pages_crawled, stats.chunks_created, stats.chunks_stored,
        )
        return 0

    except Exception:
        logger.error("WEBSITE RAG INGESTION FAILED\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
