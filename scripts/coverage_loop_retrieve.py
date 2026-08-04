"""Dump the retrieval evidence a coverage-loop B-answer must carry, without an LLM key.

Design-doc step 6 needs the fixed 30-question set run through A/B/D. B is the
knowledge-only answer, and its value to D comes from two separable halves:

1. retrieval — which chunks the retriever actually surfaced. Local and key-free
   (`intfloat/multilingual-e5-small` + Chroma), so it can be run for real here.
2. generation — turning those chunks into an answer. That needs a chat model.

This script runs half 1 exactly as the product's `retrieve` node does (same top_k,
same BM25 rescue, same neighbor expansion, same rerank) and writes the result out,
so half 2 can be performed by a substitute generator and recombined without anyone
inventing retrieval evidence that was never produced. Synthesizing `retrieved_chunks`
for an injected B-answer would put fabricated evidence in front of D, which is the
one thing the failure taxonomy cannot survive.

Outputs (into --out-dir):
- `retrieval-context.json`  full chunk text per question, formatted exactly as the
  generate node would see it. Feed this to the substitute generator; it is not
  committed because it duplicates the corpus.
- `retrieval-sources.json`  public source dicts (240-char snippets) plus the
  `RetrievedChunk` locators. This is what the ledger persists, so it is the part
  worth version-controlling as run evidence.

Usage:
    uv run python -m scripts.coverage_loop_retrieve \
        --questions data/eval/coverage-loop-30-questions.json \
        --out-dir <dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.quality.store import WorkbenchStore
from src.quality.workbench import QualityWorkbench, retrieved_chunks_from_sources
from src.rag import (
    NEIGHBOR_WINDOW,
    TOP_K,
    _format_context,
    bm25_rescue_search,
    engine_fingerprint,
    expand_with_neighbor_chunks,
    get_or_build_index,
    rerank_retrieved_documents,
)


def retrieve_documents(vectorstore: Any, question: str) -> list[tuple[Any, float]]:
    """Reproduce `src.rag.build_graph`'s retrieve node stage for stage.

    Kept as a copy rather than imported because that node is a closure inside the
    compiled graph, and building the graph constructs a chat model — which is
    exactly the thing that is unavailable without an API key. Any change to the
    node's stages must be mirrored here or the evidence stops matching production.
    """
    results = vectorstore.similarity_search_with_relevance_scores(question, k=TOP_K)
    results = bm25_rescue_search(vectorstore, question, results, max_results=TOP_K * 2)
    results = expand_with_neighbor_chunks(
        vectorstore,
        results,
        window=NEIGHBOR_WINDOW,
        max_results=len(results) * (1 + 2 * NEIGHBOR_WINDOW),
    )
    return rerank_retrieved_documents(question, results, max_results=TOP_K * 3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    payload = json.loads(args.questions.read_text(encoding="utf-8"))
    questions = payload["questions"]

    store = WorkbenchStore()
    fingerprint = engine_fingerprint()
    revision = store.bootstrap_active_revision(engine_fingerprint=fingerprint)
    revision_id = str(revision["id"])
    persist_dir = store.runtime_root / "revisions" / revision_id / "indexes" / fingerprint
    vectorstore, chunks_indexed = get_or_build_index(
        Path(str(revision["source_path"])),
        persist_dir,
        collection_name=f"revision_{revision_id}",
    )

    contexts: list[dict[str, Any]] = []
    sources_only: list[dict[str, Any]] = []
    for item in questions:
        question = str(item["question"]).strip()
        documents = retrieve_documents(vectorstore, question)
        # `_evidence` is the same private/public split `answer_question` persists, so
        # the injected B-answer carries byte-identical source dicts to a real run.
        _private, public_sources = QualityWorkbench._evidence(documents)
        chunks = [chunk.model_dump() for chunk in retrieved_chunks_from_sources(public_sources)]
        contexts.append(
            {
                "id": item["id"],
                "role": item["role"],
                "question": question,
                # Byte-for-byte what the generate node puts in front of the model.
                "context": _format_context(documents),
            }
        )
        sources_only.append(
            {
                "id": item["id"],
                "role": item["role"],
                "question": question,
                "sources": public_sources,
                "retrieved_chunks": chunks,
            }
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "revision_id": revision_id,
        "revision_label": revision["label"],
        "source_sha256": revision["source_sha256"],
        "engine_fingerprint": fingerprint,
        "chunks_indexed": chunks_indexed,
        "top_k": TOP_K,
        "neighbor_window": NEIGHBOR_WINDOW,
        "question_set": str(args.questions),
        "structured_hits": (
            "none — structured extraction needs an LLM key and was not run, so this "
            "run exercises the text-chunk lane only"
        ),
    }
    (args.out_dir / "retrieval-context.json").write_text(
        json.dumps({"meta": meta, "items": contexts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.out_dir / "retrieval-sources.json").write_text(
        json.dumps({"meta": meta, "items": sources_only}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({**meta, "questions": len(contexts)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
