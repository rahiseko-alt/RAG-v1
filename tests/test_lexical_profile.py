"""Prove the retrieval vocabulary comes from config, not from `src/rag`.

Green tests are not enough here: if `src/rag` still carried the jujutsu terms,
most of these would pass anyway on the shipped config. Each test therefore drives
a config that *differs* from the shipped one, so a hardcoded fallback would fail
it. The last test names the terms directly as a regression guard.
"""
import os
import sys

import pytest
from langchain_core.documents import Document

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.knowledge_config import (  # noqa: E402
    LANGUAGE_GENERIC_TERMS,
    LANGUAGE_STOP_TERMS,
    LexicalProfile,
    get_lexical_profile,
    load_knowledge_config,
)
from src.rag import _intent_terms, _query_terms, rerank_retrieved_documents  # noqa: E402

_MINIMAL_KNOWLEDGE = """
[knowledge]
id = "probe"
title = "Probe"
source_path = "{source}"
collection = "knowledge_probe"
example_question = "何の文書ですか？"
"""


def _write_config(tmp_path, lexical: str = "") -> str:
    source = tmp_path / "probe.md"
    source.write_text("# Probe\n\n本文。", encoding="utf-8")
    config = tmp_path / "knowledge.toml"
    config.write_text(
        _MINIMAL_KNOWLEDGE.format(source=source.as_posix()).strip() + "\n" + lexical,
        encoding="utf-8",
    )
    return str(config)


@pytest.fixture
def activate(monkeypatch, tmp_path):
    """Point the whole engine at a purpose-built config for one test."""

    def _activate(lexical: str = "") -> None:
        monkeypatch.setenv("KNOWLEDGE_CONFIG_PATH", _write_config(tmp_path, lexical))

    return _activate


def test_stop_terms_come_from_config_and_extend_the_language_defaults(activate):
    activate('[lexical]\nstop_terms = ["治験薬"]\n')

    # The configured domain term is dropped...
    assert "治験薬" not in _query_terms("治験薬の投与量は？")
    # ...while the language defaults still apply without being restated.
    assert LANGUAGE_STOP_TERMS <= get_lexical_profile().stop_terms
    assert "について" not in _query_terms("投与量について教えて")
    assert "投与量" in _query_terms("治験薬の投与量は？")


def test_intents_are_declared_by_config_not_by_the_engine(activate):
    activate(
        """
[[lexical.intents]]
name = "dosage"
triggers = ["投与量", "用量"]
"""
    )

    assert _intent_terms("推奨投与量は？") == {"dosage"}
    assert _intent_terms("用量を教えて") == {"dosage"}
    # A term the engine used to special-case is now just a word.
    assert _intent_terms("術式は？") == set()


def test_configured_markers_promote_the_answering_chunk(activate):
    """The reranking behaviour ports to another domain with config alone."""
    activate(
        """
[lexical]
generic_terms = ["投与量"]

[[lexical.intents]]
name = "dosage"
triggers = ["投与量"]
markers = ["投与量は", "1日あたり", "mg"]
paired_markers = [["投与量は", "mg"]]
proximity_terms = ["投与量", "mg"]
demoted_terms = ["投与量とは"]
"""
    )

    generic = Document(
        page_content="投与量とは、薬剤を一定期間に与える量のこと。アムロジピンも別文脈で出る。",
        metadata={"chunk_id": 1},
    )
    answer = Document(
        page_content="アムロジピンの投与量は1日あたり5mgから開始する。",
        metadata={"chunk_id": 2},
    )

    reranked = rerank_retrieved_documents(
        "アムロジピンの投与量は？",
        [(generic, 0.99), (answer, 0.80)],
        max_results=2,
    )

    assert reranked[0][0].metadata["chunk_id"] == 2
    assert reranked[0][0].metadata["rerank_score"] > reranked[1][0].metadata["rerank_score"]


def test_config_without_a_lexical_section_falls_back_to_language_defaults(activate):
    activate()

    profile = get_lexical_profile()
    assert profile.stop_terms == LANGUAGE_STOP_TERMS
    assert profile.generic_terms == LANGUAGE_GENERIC_TERMS
    assert profile.intents == ()
    # Tokenizing still works; nothing domain-specific survives.
    assert _query_terms("呪術廻戦の術式は？") == ["呪術廻戦", "術式"]


def test_editing_the_config_is_picked_up_without_a_manual_cache_reset(
    monkeypatch, tmp_path
):
    """The profile is cached; an mtime change must invalidate it."""
    path = _write_config(tmp_path, '[lexical]\nstop_terms = ["初回"]\n')
    monkeypatch.setenv("KNOWLEDGE_CONFIG_PATH", path)
    assert "初回" not in _query_terms("初回の投与量は？")

    config = tmp_path / "knowledge.toml"
    source = tmp_path / "probe.md"
    config.write_text(
        _MINIMAL_KNOWLEDGE.format(source=source.as_posix()).strip()
        + '\n[lexical]\nstop_terms = ["投与量"]\n',
        encoding="utf-8",
    )
    os.utime(config, (0, 0))  # force a distinct mtime rather than trusting the clock

    assert "初回" in _query_terms("初回の投与量は？")
    assert "投与量" not in _query_terms("初回の投与量は？")


def test_missing_config_degrades_to_defaults_but_malformed_config_raises(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("KNOWLEDGE_CONFIG_PATH", str(tmp_path / "absent.toml"))
    assert get_lexical_profile() == LexicalProfile()

    monkeypatch.setenv(
        "KNOWLEDGE_CONFIG_PATH",
        _write_config(tmp_path, '[lexical]\nstop_terms = "not-a-list-of-lists"\n[[lexical.intents]]\ntriggers = ["x"]\n'),
    )
    with pytest.raises(ValueError, match="name"):
        get_lexical_profile()


def test_shipped_config_still_carries_the_terms_the_engine_used_to_hardcode():
    """Regression guard: the port must not have dropped tuning on the way out."""
    profile = load_knowledge_config(os.path.join(_ROOT, "config", "knowledge.toml")).lexical

    assert "呪術廻戦" in profile.stop_terms
    assert {"術式", "領域", "名セリフ", "セリフ"} <= profile.generic_terms
    assert {intent.name for intent in profile.intents} == {
        "technique",
        "domain",
        "voice_actor",
        "creator",
    }
    technique = next(intent for intent in profile.intents if intent.name == "technique")
    assert "反転術式" in technique.demoted_terms
    assert ("術式「", "使い手") in technique.paired_markers


def test_engine_source_holds_no_knowledge_specific_terms():
    """The point of the change: `src/rag` must not name the demo knowledge."""
    source = open(os.path.join(_ROOT, "src", "rag", "__init__.py"), encoding="utf-8").read()

    for term in ("呪術廻戦", "術式", "領域", "声優", "作者", "操術", "呪法"):
        assert term not in source, f"{term} が src/rag に残っています"
