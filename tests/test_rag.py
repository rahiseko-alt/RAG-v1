"""Stage 3 RAG の軽量テスト（生成=LLM 呼び出しは含めない＝APIキー不要）。

実行（リポジトリ直下 products/medguide-rag で）:
    python -m pytest tests/ -q

- test_chunking: ingest が出典 metadata 付きのチャンク列を返すこと
- test_retrieval_finds_relevant: 日本語質問で関連チャンクを上位に返すこと
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.ingest import load_and_chunk  # noqa: E402
from src.knowledge_config import get_active_knowledge  # noqa: E402
from src.rag import get_default_source, get_or_build_index  # noqa: E402


def test_document_present():
    source = get_default_source()
    assert source.exists(), f"題材文書が無い: {source}"


def test_chunking():
    chunks = load_and_chunk(get_default_source())
    assert len(chunks) >= 3, f"チャンク数が想定より少ない: {len(chunks)}"
    # 出典追跡に必要な metadata が全チャンクに付いていること
    for c in chunks:
        assert "source" in c.metadata
        assert "page" in c.metadata
        assert "chunk_id" in c.metadata
        assert len(c.page_content.strip()) >= 30  # 空白のみチャンクは除外済み


@pytest.mark.slow
def test_retrieval_finds_relevant():
    """埋め込みモデルのロードが要るため slow。設定された例示質問で関連チャンクが上位に来る。"""
    knowledge = get_active_knowledge()
    vs, n = get_or_build_index()
    assert n >= 3
    hits = vs.similarity_search_with_relevance_scores(knowledge.example_question, k=4)
    assert hits, "検索結果が空"
    joined = " ".join(d.page_content for d, _ in hits)
    assert any(term in joined for term in knowledge.expected_terms)
