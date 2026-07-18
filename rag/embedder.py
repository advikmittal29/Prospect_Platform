"""
Gemini embedding wrapper.

Uses google.genai (v2.x) — the same SDK used by the rest of the project.
Does NOT use the deprecated google.generativeai package.

task_type semantics:
  RETRIEVAL_DOCUMENT — use when embedding chunks during indexing
  RETRIEVAL_QUERY    — use when embedding a user query at retrieval time
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

logger = logging.getLogger("prospect.rag.embedder")


class GeminiEmbedder:
    """
    Wraps client.models.embed_content for both indexing and querying.
    Instantiate once and reuse — the underlying genai.Client is stateless.
    """

    def __init__(self, api_key: str, model: str, output_dimensionality: int) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model  = model
        self._dims   = output_dimensionality

    def embed_document(self, text: str, title: str = "") -> List[float]:
        """Embed a single document chunk for indexing."""
        return self._embed_batch_raw([text], task_type="RETRIEVAL_DOCUMENT")[0]

    def embed_query(self, text: str) -> List[float]:
        """Embed a query string for retrieval."""
        return self._embed_batch_raw([text], task_type="RETRIEVAL_QUERY")[0]

    # Free-tier: 100 req/min; each text in a batch = 1 request.
    # 15 texts per batch × ~4.5s pause = ~13 batches/min = ~195 req/min → too fast.
    # 15 texts per batch + 15s pause = 60 req/min → safely under 100 RPM.
    _BATCH_LIMIT = 15
    _BATCH_PAUSE = 15.0  # seconds between batches

    def embed_documents_batch(
        self,
        texts: List[str],
        titles: List[str] | None = None,
        on_batch: Optional[Callable[[int, int], None]] = None,
    ) -> List[List[float]]:
        """
        Embed chunks in sub-batches of 15 with a 15s pause between each.
        Rate: 15 texts × 4 batches/min = 60 req/min — safely under the 100 RPM cap.
        On 429, extracts the retry-after delay from the error and honors it exactly.

        `on_batch(embedded_so_far, total)` is called after each batch completes,
        for live progress reporting.
        """
        import re
        import time

        if not texts:
            return []

        all_vecs: List[List[float]] = []
        total = len(texts)
        num_batches = (total + self._BATCH_LIMIT - 1) // self._BATCH_LIMIT

        for batch_idx, start in enumerate(range(0, total, self._BATCH_LIMIT)):
            batch = texts[start: start + self._BATCH_LIMIT]
            batch_num = batch_idx + 1
            last_exc = None

            for attempt in range(1, 6):
                try:
                    vecs = self._embed_batch_raw(batch, task_type="RETRIEVAL_DOCUMENT")
                    all_vecs.extend(vecs)
                    embedded_so_far = min(start + self._BATCH_LIMIT, total)
                    logger.info(
                        "Embedded %d/%d chunks (batch %d/%d).",
                        embedded_so_far, total, batch_num, num_batches,
                    )
                    if on_batch is not None:
                        on_batch(embedded_so_far, total)
                    break
                except Exception as exc:
                    last_exc = exc
                    # Extract retry-after from 429 error message if present
                    wait = self._retry_wait(str(exc), attempt)
                    logger.warning(
                        "Batch %d/%d attempt %d failed (%s). Waiting %ds…",
                        batch_num, num_batches, attempt, type(exc).__name__, wait,
                    )
                    time.sleep(wait)
            else:
                raise RuntimeError(
                    f"Batch {batch_num}/{num_batches} failed after 5 attempts: {last_exc}"
                ) from last_exc

            # Pause between batches to stay under RPM cap
            if batch_num < num_batches:
                time.sleep(self._BATCH_PAUSE)

        return all_vecs

    @staticmethod
    def _retry_wait(error_str: str, attempt: int) -> float:
        """
        If the 429 error message includes 'retry in Xs', honour that delay + 2s buffer.
        Otherwise use exponential backoff: 10, 20, 30, 40, 50 seconds.
        """
        import re
        m = re.search(r"retry in\s+([\d.]+)s", error_str, re.IGNORECASE)
        if m:
            return float(m.group(1)) + 2.0
        return 10.0 * attempt

    # ------------------------------------------------------------------

    def _embed_batch_raw(self, texts: List[str], task_type: str) -> List[List[float]]:
        """
        Call embed_content with a list of texts — maps to a single
        batchEmbedContents POST, regardless of list length.
        Returns a list of float vectors in the same order as texts.
        """
        from google.genai import types

        response = self._client.models.embed_content(
            model=self._model,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self._dims,
            ),
        )
        embeddings = response.embeddings
        if not embeddings:
            raise ValueError("Gemini returned no embeddings.")
        return [list(e.values) for e in embeddings]
