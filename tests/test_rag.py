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

import src.rag as rag  # noqa: E402
from src.ingest import load_and_chunk  # noqa: E402
from src.knowledge_config import get_active_knowledge  # noqa: E402
from src.rag import (  # noqa: E402
    bm25_rescue_search,
    expand_with_neighbor_chunks,
    get_default_source,
    get_or_build_index,
    normalize_answer_citations,
    rerank_retrieved_documents,
)
from langchain_core.documents import Document  # noqa: E402


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


def test_missing_sentence_citation_is_not_invented():
    answer = (
        "東堂葵の術式は不義遊戯です。"
        "拍手を契機に対象の位置を入れ替えます。[1]"
    )

    normalized = normalize_answer_citations(answer)

    assert normalized == (
        "東堂葵の術式は不義遊戯です。"
        "拍手を契機に対象の位置を入れ替えます [1]。"
    )


def test_citation_normalization_preserves_decimal_points():
    normalized = normalize_answer_citations("用量は2.5 mgです。[2]")

    assert normalized == "用量は2.5 mgです [2]。"


def test_neighbor_expansion_keeps_adjacent_context_from_same_source():
    class FakeVectorStore:
        def get(self, where, include):
            assert where == {"chunk_id": {"$in": [1, 3]}}
            assert include == ["documents", "metadatas"]
            return {
                "documents": ["前", "人物紹介", "術式は不義遊戯", "別ページ"],
                "metadatas": [
                    {"chunk_id": 1, "source": "a", "page": 0},
                    {"chunk_id": 2, "source": "a", "page": 0},
                    {"chunk_id": 3, "source": "a", "page": 0},
                    {"chunk_id": 4, "source": "b", "page": 0},
                ],
            }

    anchor = Document(
        page_content="人物紹介",
        metadata={"chunk_id": 2, "source": "a", "page": 0},
    )

    expanded = expand_with_neighbor_chunks(FakeVectorStore(), [(anchor, 0.9)], window=1)

    assert [document.metadata["chunk_id"] for document, _score in expanded] == [2, 3, 1]
    assert expanded[1][0].metadata["retrieval_relation"] == "neighbor"
    assert expanded[1][0].metadata["anchor_chunk_id"] == 2


def test_bm25_rescue_adds_entity_chunk_when_vector_search_misses_it():
    class FakeVectorStore:
        def get(self, include):
            assert include == ["documents", "metadatas"]
            return {
                "documents": [
                    "術式とは呪力を流して発動する特殊能力。",
                    "冥冥（めいめい）は1級呪術師。",
                    "烏を操る術式「黒鳥操術」の使い手。",
                ],
                "metadatas": [
                    {"chunk_id": 10, "source": "terms", "page": 0},
                    {"chunk_id": 20, "source": "characters", "page": 0},
                    {"chunk_id": 21, "source": "characters", "page": 0},
                ],
            }

    vector_hit = Document(
        page_content="術式とは呪力を流して発動する特殊能力。",
        metadata={"chunk_id": 10, "source": "terms", "page": 0},
    )

    rescued = bm25_rescue_search(
        FakeVectorStore(),
        "冥々の術式は？",
        [(vector_hit, 0.88)],
        max_results=4,
    )

    chunk_ids = [document.metadata["chunk_id"] for document, _score in rescued]
    assert 20 in chunk_ids
    assert any(
        document.metadata.get("retrieval_relation") == "bm25_rescue"
        for document, _score in rescued
        if document.metadata["chunk_id"] == 20
    )
    assert any(
        document.metadata.get("bm25_score")
        for document, _score in rescued
        if document.metadata["chunk_id"] == 20
    )


def test_query_terms_match_across_spacing_width_and_case_variants():
    """表記が違うだけの質問と本文が、検索語の一致まで到達すること。

    正規化関数単体のテスト（tests/test_text_normalize.py）は等価性しか見ておらず、
    それが `_query_terms` と本文照合まで実際に効いているかは別問題なので、
    ここは**検索語が本文に一致すること**を直接確かめる。
    """
    from src.knowledge_config import LexicalProfile
    from src.text_normalize import normalize_for_matching

    profile = LexicalProfile()
    # 質問は空白あり・全角、本文は詰め・半角。正規化が無ければ一致しない組み合わせ。
    terms = rag._query_terms("ＣＯＶＩＤ-19 では 500 mg を投与しますか", profile=profile)
    text = normalize_for_matching("covid-19の患者には500mgを投与する")

    matched = [term for term in terms if term in text]
    assert "500mg" in matched, f"単位前の空白が吸収されていない: {terms}"
    assert "covid-19" in matched, f"全角・大小文字が吸収されていない: {terms}"


def test_rerank_promotes_answer_pattern_over_generic_term_match():
    """The answer-shaped chunk wins on its wording alone.

    Both documents carry the same entity, the same vector score and the same
    retrieval metadata, so every other term in `_lexical_rerank_score` cancels out
    and only the configured intent markers can decide the order. An earlier version
    of this test gave the answer chunk a bm25 bonus the generic one lacked, and so
    passed even with the intent config removed entirely.
    """
    shared_metadata = {"retrieval_relation": "bm25_rescue", "bm25_score": 2.5}
    generic = Document(
        page_content="術式とは呪力を流して発動する特殊能力。冥冥の名前も別文脈で出る。",
        metadata={"chunk_id": 1, **shared_metadata},
    )
    answer = Document(
        page_content="冥冥は1級術師。烏を操る術式「黒鳥操術」の使い手。",
        metadata={"chunk_id": 2, **shared_metadata},
    )

    reranked = rerank_retrieved_documents(
        "冥々の術式は？",
        [(generic, 0.99), (answer, 0.99)],
        max_results=2,
    )

    assert reranked[0][0].metadata["chunk_id"] == 2
    assert reranked[0][0].metadata["rerank_score"] > reranked[1][0].metadata["rerank_score"]


def test_langchain_constructor_kwargs_still_exist():
    """Guard the two `# type: ignore[call-arg]` sites in `src/rag/__init__.py`.

    Those ignores exist because langchain aliases the pydantic fields we pass
    (`model_name`/`model`, `max_tokens`/`max_tokens_to_sample`) and the plugin builds
    `__init__` from the alias, so mypy rejects field names that work at runtime. The
    cost is that the ignore covers the whole call: any *other* bad keyword on those
    two lines would also stop being reported. This asserts each keyword we actually
    pass is still accepted, so a dependency bump that renames or drops one fails CI
    here rather than at runtime — the embedding path only loads under `-m slow`, and
    the chat path only with an API key, so neither is exercised by the default run.

    Reads class metadata only; it never constructs either object, so no model
    download and no network.
    """
    from langchain_anthropic import ChatAnthropic
    from langchain_huggingface import HuggingFaceEmbeddings

    for cls, passed in (
        (HuggingFaceEmbeddings, ("model_name", "encode_kwargs")),
        (ChatAnthropic, ("model", "max_tokens", "timeout", "stop")),
    ):
        # Field names (as opposed to aliases) are only accepted because of this.
        assert cls.model_config.get("populate_by_name") is True, cls.__name__
        aliases = {field.alias for field in cls.model_fields.values() if field.alias}
        for keyword in passed:
            assert keyword in cls.model_fields or keyword in aliases, (
                f"{cls.__name__} no longer accepts {keyword!r}; "
                "src/rag/__init__.py passes it under a call-arg ignore"
            )


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
