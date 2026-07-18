"""
Section-aware text chunker.

Strategy:
  1. Split on heading-like boundaries (blank-line + ALL-CAPS line, or markdown #)
     to keep semantically coherent sections together.
  2. Within each section, slide a window of ~CHUNK_TOKENS tokens with OVERLAP_TOKENS
     overlap so context carries across chunk boundaries.
  3. Token counting is approximated as len(text.split()) * 1.3 (good enough;
     avoids a tiktoken/sentencepiece dependency).

Each chunk is returned as a dict ready to pass to the vector store:
  {
    "text":       str,
    "source":     "website",
    "source_url": str,
    "title":      str,   # page title or inferred heading
  }
"""
from __future__ import annotations

import re
from typing import List, Dict, Any

CHUNK_TOKENS   = 400
OVERLAP_TOKENS = 50

_WORDS_PER_TOKEN = 0.75   # 1 token ≈ 0.75 words → 1 word ≈ 1.33 tokens


def _word_count_to_tokens(words: int) -> float:
    return words / _WORDS_PER_TOKEN


def _split_into_sections(text: str) -> List[str]:
    """
    Split on markdown headings or blank-line-separated all-caps lines.
    Falls back to paragraph breaks.
    """
    # Markdown headings (# Heading)
    md_split = re.split(r"\n(?=#{1,4}\s)", text)
    if len(md_split) > 1:
        return [s.strip() for s in md_split if s.strip()]

    # Blank line separating an ALL-CAPS / Title Case heading
    para_split = re.split(r"\n{2,}", text)
    if len(para_split) > 1:
        return [s.strip() for s in para_split if s.strip()]

    return [text.strip()]


def _slide_window(words: List[str], chunk_words: int, overlap_words: int) -> List[str]:
    """Sliding window over a word list, yielding joined chunks."""
    chunks = []
    start  = 0
    step   = max(1, chunk_words - overlap_words)
    while start < len(words):
        end = start + chunk_words
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk.strip())
        if end >= len(words):
            break
        start += step
    return chunks


def chunk_text(
    text: str,
    source_url: str,
    title: str,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> List[Dict[str, Any]]:
    """
    Split `text` into overlapping chunks and attach metadata.
    Returns a list of chunk dicts.
    """
    chunk_words   = int(chunk_tokens   * _WORDS_PER_TOKEN)
    overlap_words = int(overlap_tokens * _WORDS_PER_TOKEN)

    sections  = _split_into_sections(text)
    result: List[Dict[str, Any]] = []

    for section in sections:
        words = section.split()
        if not words:
            continue

        # Infer a section heading (first line if short enough)
        lines = section.splitlines()
        heading = lines[0].lstrip("#").strip() if lines else title
        if len(heading) > 120 or len(heading.split()) > 15:
            heading = title

        sub_chunks = _slide_window(words, chunk_words, overlap_words)
        for chunk in sub_chunks:
            if len(chunk.split()) < 10:
                continue
            result.append({
                "text":       chunk,
                "source":     "website",
                "source_url": source_url,
                "title":      heading or title,
            })

    return result
