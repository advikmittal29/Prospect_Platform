"""
Vector store abstraction.

VectorStore is a thin ABC so callers never import ChromaDB directly.
ChromaVectorStore is the concrete implementation backed by a local persistent Chroma DB.

We always provide pre-computed embeddings (never rely on ChromaDB's built-in
embedding function) so the embedding model can be swapped without touching this file.
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prospect.rag.store")


class VectorStore(ABC):
    @abstractmethod
    def add(
        self,
        texts: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
    ) -> None:
        """Persist a batch of text chunks with their embeddings and metadata."""

    @abstractmethod
    def query(
        self,
        query_embedding: List[float],
        k: int = 4,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return up to k nearest chunks.
        Each result: {"text": str, "metadata": dict, "score": float}
        score is cosine distance (lower = more similar).
        """

    @abstractmethod
    def reset_source(self, source: str) -> None:
        """Delete all chunks whose metadata["source"] == source."""

    @abstractmethod
    def count(self) -> int:
        """Total number of chunks currently stored."""


class ChromaVectorStore(VectorStore):
    """
    Persistent ChromaDB-backed vector store.

    We pass embedding_function=None to every collection call because we
    always supply pre-computed embeddings. This avoids the default
    sentence-transformers download and keeps the dependency surface minimal.
    """

    def __init__(self, persist_path: str, collection_name: str) -> None:
        import chromadb

        path = Path(persist_path)
        path.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=str(path))
        self._col = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "ChromaVectorStore ready — collection=%r path=%s chunks=%d",
            collection_name, persist_path, self._col.count(),
        )

    # ------------------------------------------------------------------

    def add(
        self,
        texts: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
    ) -> None:
        if not texts:
            return
        ids = [_chunk_id(meta, text) for meta, text in zip(metadatas, texts)]

        # Deduplicate within this batch — identical text on multiple pages
        # produces the same content-hash ID, which ChromaDB rejects even in upsert.
        seen: set = set()
        u_ids, u_embs, u_texts, u_metas = [], [], [], []
        for id_, emb, txt, meta in zip(ids, embeddings, texts, metadatas):
            if id_ not in seen:
                seen.add(id_)
                u_ids.append(id_)
                u_embs.append(emb)
                u_texts.append(txt)
                u_metas.append(meta)

        dupes = len(ids) - len(u_ids)
        if dupes:
            logger.debug("Skipped %d duplicate chunk(s) before write.", dupes)

        self._col.upsert(
            ids=u_ids,
            embeddings=u_embs,
            documents=u_texts,
            metadatas=u_metas,
        )
        logger.debug("Stored %d chunk(s) (%d dupes skipped).", len(u_ids), dupes)

    def query(
        self,
        query_embedding: List[float],
        k: int = 4,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        total = self._col.count()
        if total == 0:
            return []
        n = min(k, total)
        kwargs: Dict[str, Any] = dict(
            query_embeddings=[query_embedding],
            n_results=n,
            include=["documents", "metadatas", "distances"],
        )
        if where:
            kwargs["where"] = where
        result = self._col.query(**kwargs)
        out = []
        docs      = (result.get("documents") or [[]])[0]
        metas     = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        for doc, meta, dist in zip(docs, metas, distances):
            out.append({"text": doc, "metadata": meta or {}, "score": dist})
        return out

    def reset_source(self, source: str) -> None:
        try:
            before = self._col.count()
            self._col.delete(where={"source": source})
            after = self._col.count()
            logger.info(
                "reset_source(%r): removed %d chunk(s), %d remaining.",
                source, before - after, after,
            )
        except Exception as exc:
            logger.warning("reset_source(%r) failed: %s", source, exc)

    def count(self) -> int:
        return self._col.count()


def _chunk_id(meta: Dict[str, Any], text: str) -> str:
    """Deterministic ID: hash of (source_url, text). Stable across re-ingests."""
    key = f"{meta.get('source_url', '')}::{text}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
