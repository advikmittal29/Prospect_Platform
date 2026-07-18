"""Quick retrieval smoke test — run once after ingestion."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AppConfig
from rag.retriever import Retriever

cfg = AppConfig()
r = Retriever(rag_cfg=cfg.rag, llm_cfg=cfg.llm)

queries = [
    ("what do you do?",                                      True),
    ("who are you / about your company",                     True),
    ("how many clients do you have? what is your success rate?", False),  # not on site
]

for query, expect_results in queries:
    print(f"\n{'='*60}")
    print(f"QUERY: {query}")
    print(f"{'='*60}")
    chunks = r.retrieve(query)
    if not chunks:
        print("  (no results returned)")
    for i, c in enumerate(chunks, 1):
        score = c["score"]
        url   = c["metadata"].get("source_url", "")
        title = c["metadata"].get("title", "")
        text  = c["text"][:200]
        print(f"  [{i}] score={score:.3f} | {title[:40]} | {url.split('/')[-2]}")
        print(f"       {text}")
        print()
