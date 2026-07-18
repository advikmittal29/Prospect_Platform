"""
Retriever: given a query string, return the top-k most relevant chunks
from the vector store.

Used by LLMReplyGenerator to ground replies in verified company facts.
Returns [] gracefully on any error so callers always degrade safely.
"""
from __future__ import annotations

import logging
from typing import List, Dict, Any, Optional

from config import RAGConfig, LLMConfig
from rag.embedder import GeminiEmbedder
from rag.store    import ChromaVectorStore

logger = logging.getLogger("prospect.rag.retriever")


class Retriever:
    def __init__(self, rag_cfg: RAGConfig, llm_cfg: LLMConfig) -> None:
        self._cfg = rag_cfg
        self._embedder: Optional[GeminiEmbedder] = None
        self._store:    Optional[ChromaVectorStore] = None
        self._ready = False

        try:
            self._embedder = GeminiEmbedder(
                api_key               = llm_cfg.api_key,
                model                 = rag_cfg.embed_model,
                output_dimensionality = rag_cfg.embed_dims,
            )
            self._store = ChromaVectorStore(
                persist_path    = rag_cfg.chroma_path,
                collection_name = rag_cfg.collection_name,
            )
            count = self._store.count()
            if count == 0:
                logger.warning(
                    "RAG store is empty (%s). Run `python -m rag.ingest_website --url <URL>` first.",
                    rag_cfg.chroma_path,
                )
            else:
                logger.info("Retriever ready — %d chunk(s) in store.", count)
                self._ready = True
        except Exception as exc:
            logger.warning("Retriever init failed (RAG will be skipped): %s", exc)

    def retrieve(self, query: str, k: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Return up to k chunks most relevant to `query`.
        Each result: {"text": str, "metadata": dict, "score": float}

        Returns [] on any error so the caller can degrade gracefully.
        """
        if not self._ready or not self._embedder or not self._store:
            return []
        if not query or not query.strip():
            return []

        top_k = k if k is not None else self._cfg.top_k
        try:
            vec = self._embedder.embed_query(query)
            results = self._store.query(query_embedding=vec, k=top_k)
            logger.debug("RAG retrieved %d chunk(s) for query: %r", len(results), query[:60])
            return results
        except Exception as exc:
            logger.warning("RAG retrieval failed (skipping): %s", exc)
            return []
