import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.knowledge_config import load_knowledge_config  # noqa: E402


def test_load_knowledge_config_from_custom_toml(tmp_path):
    source = tmp_path / "client.md"
    source.write_text("# Client Knowledge\n\nテスト用ナレッジです。", encoding="utf-8")
    eval_set = tmp_path / "client-questions.json"
    eval_set.write_text('{"questions":[]}', encoding="utf-8")
    config = tmp_path / "knowledge.toml"
    config.write_text(
        f"""
[knowledge]
id = "client"
title = "Client Knowledge"
source_path = "{source.as_posix()}"
collection = "knowledge_client"
source_url = "https://example.com"
license = "internal"
checked_at = "2026-07-31"
eval_set = "{eval_set.as_posix()}"
example_question = "何の文書ですか？"
expected_terms = ["テスト"]
""".strip(),
        encoding="utf-8",
    )

    knowledge = load_knowledge_config(config)

    assert knowledge.id == "client"
    assert knowledge.source_path == source
    assert knowledge.eval_set == eval_set
    assert knowledge.collection == "knowledge_client"
    assert "テスト" in knowledge.expected_terms
