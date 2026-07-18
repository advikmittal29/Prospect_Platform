"""
Ingestion orchestrator: crawl → chunk → embed → store.

Re-runnable: on each call we delete all existing "website" chunks for the
given URL's origin before re-adding fresh ones, so the store never drifts
from the live site.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

from config import RAGConfig, LLMConfig
from rag.chunker  import chunk_text
from rag.crawler  import WebsiteCrawler
from rag.embedder import GeminiEmbedder
from rag.store    import ChromaVectorStore

logger = logging.getLogger("prospect.rag.ingest")


@dataclass
class IngestStats:
    pages_crawled:  int = 0
    chunks_created: int = 0
    chunks_stored:  int = 0


def ingest_website(
    url:         str,
    rag_cfg:     RAGConfig,
    llm_cfg:     LLMConfig,
    *,
    max_depth:   int   = 3,
    dry_run:     bool  = False,
    on_progress: Optional[Callable[[dict], None]] = None,
) -> IngestStats:
    """
    Full pipeline: crawl `url` → chunk pages → embed → write to ChromaDB.

    dry_run=True crawls and chunks but skips embedding + storing (useful for
    testing the crawler without burning API quota).

    `on_progress(dict)` is called repeatedly during the crawl and embed phases
    with real counts (never a fake/timer-based estimate):
      {"stage": "crawling", "pages_crawled": N, "pages_in_queue": N}
      {"stage": "embedding", "chunks_embedded": N, "chunks_created": N}
    """
    stats = IngestStats()

    # 1. Crawl
    logger.info("[INGEST] Starting crawl: %s (max_depth=%d)", url, max_depth)
    crawler = WebsiteCrawler(seed_url=url, max_depth=max_depth)

    def _on_page(pages_fetched: int, queue_remaining: int) -> None:
        if on_progress is not None:
            on_progress({
                "stage": "crawling",
                "pages_crawled": pages_fetched,
                "pages_in_queue": queue_remaining,
            })

    pages = crawler.crawl(on_page=_on_page)
    stats.pages_crawled = len(pages)
    logger.info("[INGEST] Crawled %d page(s).", stats.pages_crawled)

    if not pages:
        logger.warning("[INGEST] No pages retrieved — nothing to ingest.")
        return stats

    # 2. Chunk
    all_chunks = []
    for page in pages:
        chunks = chunk_text(
            text       = page.text,
            source_url = page.url,
            title      = page.title,
        )
        all_chunks.extend(chunks)
        logger.debug("[INGEST] %s → %d chunk(s)", page.url, len(chunks))

    stats.chunks_created = len(all_chunks)
    logger.info("[INGEST] %d chunk(s) created from %d page(s).", stats.chunks_created, stats.pages_crawled)

    if dry_run:
        logger.info("[INGEST] dry_run=True — skipping embed + store.")
        return stats

    if not all_chunks:
        logger.warning("[INGEST] No chunks to embed.")
        return stats

    # 3. Embed
    embedder = GeminiEmbedder(
        api_key              = llm_cfg.api_key,
        model                = rag_cfg.embed_model,
        output_dimensionality = rag_cfg.embed_dims,
    )

    texts     = [c["text"]  for c in all_chunks]
    titles    = [c["title"] for c in all_chunks]
    metadatas = [
        {k: v for k, v in c.items() if k != "text"}
        for c in all_chunks
    ]

    def _on_batch(embedded_so_far: int, total: int) -> None:
        if on_progress is not None:
            on_progress({
                "stage": "embedding",
                "chunks_embedded": embedded_so_far,
                "chunks_created": total,
            })

    logger.info("[INGEST] Embedding %d chunk(s) via %s…", len(texts), rag_cfg.embed_model)
    embeddings = embedder.embed_documents_batch(texts, titles=titles, on_batch=_on_batch)

    # 4. Store — clear previous website data first (idempotency)
    store = ChromaVectorStore(
        persist_path    = rag_cfg.chroma_path,
        collection_name = rag_cfg.collection_name,
    )
    store.reset_source("website")
    store.add(texts=texts, embeddings=embeddings, metadatas=metadatas)
    stats.chunks_stored = store.count()

    logger.info(
        "[INGEST] Done. pages=%d  chunks_created=%d  chunks_stored=%d",
        stats.pages_crawled, stats.chunks_created, stats.chunks_stored,
    )
    return stats
