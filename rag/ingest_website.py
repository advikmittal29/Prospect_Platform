"""
CLI entry point for website ingestion.

Usage:
    python -m rag.ingest_website --url https://gnxtsystems.com/
    python -m rag.ingest_website --url https://gnxtsystems.com/ --depth 2
    python -m rag.ingest_website --url https://gnxtsystems.com/ --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

# Ensure project root on sys.path when run as __main__
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AppConfig
from rag.ingest import ingest_website

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("prospect.rag.ingest_website")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest a website into the RAG vector store."
    )
    parser.add_argument("--url",      required=True, help="Seed URL to crawl")
    parser.add_argument("--depth",    type=int, default=3, help="Max crawl depth (default 3)")
    parser.add_argument("--dry-run",  action="store_true", help="Crawl + chunk only, skip embed+store")
    args = parser.parse_args()

    config = AppConfig()

    if not config.llm.api_key:
        logger.error("LLM_API_KEY is not set — cannot call Gemini embedding API.")
        sys.exit(1)

    t0 = time.time()
    stats = ingest_website(
        url       = args.url,
        rag_cfg   = config.rag,
        llm_cfg   = config.llm,
        max_depth = args.depth,
        dry_run   = args.dry_run,
    )
    elapsed = time.time() - t0

    print()
    print("=" * 55)
    print("  Ingestion complete")
    print("=" * 55)
    print(f"  Pages crawled  : {stats.pages_crawled}")
    print(f"  Chunks created : {stats.chunks_created}")
    print(f"  Chunks stored  : {stats.chunks_stored}")
    print(f"  Elapsed        : {elapsed:.1f}s")
    print("=" * 55)
    if args.dry_run:
        print("  (dry-run — nothing written to vector store)")
    print()


if __name__ == "__main__":
    main()
