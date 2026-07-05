"""Stage 3 RAG の軽量テスト（生成=LLM 呼び出しは含めない＝APIキー不要）。

実行（リポジトリ直下 products/medguide-rag で）:
    python -m pytest tests/ -q

- test_chunking: ingest が出典 metadata 付きのチャンク列を返すこと
- test_retrieval_finds_relevant: 日本語質問→英語文書のクロスリンガル検索が関連チャンクを上位に返すこと
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.ingest import load_and_chunk  # noqa: E402
from src.rag import DEFAULT_PDF, get_or_build_index  # noqa: E402


def test_document_present():
    assert DEFAULT_PDF.exists(), f"題材PDFが無い: {DEFAULT_PDF}"


def test_chunking():
    chunks = load_and_chunk(DEFAULT_PDF)
    assert len(chunks) >= 30, f"チャンク数が想定より少ない: {len(chunks)}"
    # 出典追跡に必要な metadata が全チャンクに付いていること
    for c in chunks:
        assert "source" in c.metadata
        assert "page" in c.metadata
        assert "chunk_id" in c.metadata
        assert len(c.page_content.strip()) >= 30  # 空白のみチャンクは除外済み


@pytest.mark.slow
def test_retrieval_finds_relevant():
    """埋め込みモデルのロードが要るため slow。日本語質問で運動関連チャンクが上位に来る。"""
    vs, n = get_or_build_index()
    assert n >= 30
    hits = vs.similarity_search_with_relevance_scores("運動はどのくらい推奨されますか", k=4)
    assert hits, "検索結果が空"
    joined = " ".join(d.page_content.lower() for d, _ in hits)
    # 身体活動に関する語が上位チャンクに含まれること（クロスリンガル検索の健全性）
    assert "physical activity" in joined or "exercise" in joined
