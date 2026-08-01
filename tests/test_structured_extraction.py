from langchain_core.documents import Document

from src.structured_extraction import (
    ExtractedKnowledgeBatch,
    LLMStructuredExtractor,
    extract_structured_records,
    merge_extracted_batches,
    _normalize_evidence_payload,
)
from src.structured_knowledge import KnowledgeEntity, KnowledgeFact


class FakeExtractor:
    def extract_document(self, document):
        return ExtractedKnowledgeBatch(
            entities=[
                KnowledgeEntity(
                    entity_id="",
                    name="経費承認規程",
                    entity_type="policy",
                    aliases=["経費ルール"],
                )
            ],
            facts=[
                KnowledgeFact(
                    fact_id="",
                    subject="経費承認規程",
                    predicate="approval_threshold",
                    value={"amount": 50000, "currency": "JPY", "approver": "部長"},
                )
            ],
        )


def test_extract_structured_records_fills_ids_and_evidence():
    docs = [
        Document(
            page_content="5万円以上の経費は部長が承認する。",
            metadata={"source": "policy.md", "page": 2, "chunk_id": 7},
        )
    ]

    batch = extract_structured_records(docs, extractor=FakeExtractor())

    assert batch.entities[0].entity_id.startswith("entity:")
    assert batch.facts[0].fact_id.startswith("fact:")
    assert batch.facts[0].evidence[0].source == "policy.md"
    assert batch.facts[0].evidence[0].chunk_id == 7


def test_merge_extracted_batches_deduplicates_by_record_id():
    first = ExtractedKnowledgeBatch(
        entities=[KnowledgeEntity(entity_id="entity:1", name="A")],
        facts=[KnowledgeFact(fact_id="fact:1", subject="A", predicate="is", object_text="B")],
    )
    second = ExtractedKnowledgeBatch(
        entities=[KnowledgeEntity(entity_id="entity:1", name="A duplicate")],
        facts=[KnowledgeFact(fact_id="fact:1", subject="A", predicate="is", object_text="B duplicate")],
    )

    merged = merge_extracted_batches([first, second])

    assert len(merged.entities) == 1
    assert len(merged.facts) == 1
    assert merged.entities[0].name == "A"


def test_llm_extractor_json_parser_accepts_fenced_json():
    parsed = LLMStructuredExtractor._parse_json_response(
        '```json\n{"entities":[],"facts":[]}\n```'
    )

    assert parsed == {"entities": [], "facts": []}


def test_normalize_evidence_payload_converts_string_excerpts():
    document = Document(
        page_content="本文です。",
        metadata={"source": "source.md", "chunk_id": 9, "page": 1},
    )

    payload = _normalize_evidence_payload(
        {
            "entities": [
                {
                    "entity_id": "",
                    "name": "日下部篤也",
                    "evidence": ["簡易領域を教えた"],
                }
            ],
            "facts": [
                {
                    "fact_id": "",
                    "subject": "虎杖悠仁",
                    "predicate": "learned_reason",
                    "evidence": ["魂の入れ替え修行"],
                }
            ],
        },
        document,
    )

    assert payload["entities"][0]["evidence"][0]["source"] == "source.md"
    assert payload["facts"][0]["evidence"][0]["chunk_id"] == 9
    assert payload["facts"][0]["evidence"][0]["excerpt"] == "魂の入れ替え修行"


def test_extract_structured_records_drops_unknown_subject_facts():
    class UnknownSubjectExtractor:
        def extract_document(self, document):
            return ExtractedKnowledgeBatch(
                facts=[
                    KnowledgeFact(
                        fact_id="fact:unknown",
                        subject="不明の人物",
                        predicate="行動した",
                        object_text="何か",
                    )
                ]
            )

    batch = extract_structured_records(
        [Document(page_content="本文", metadata={"source": "source.md"})],
        extractor=UnknownSubjectExtractor(),
    )

    assert batch.facts == []
