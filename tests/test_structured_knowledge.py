from src.structured_knowledge import (
    EvidenceRef,
    KnowledgeEntity,
    KnowledgeFact,
    StructuredKnowledgeStore,
    format_structured_context,
)


def test_structured_store_searches_reason_facts_without_domain_specific_columns(tmp_path):
    store = StructuredKnowledgeStore(tmp_path / "structured.sqlite3")
    evidence = EvidenceRef(
        source="source.md",
        chunk_id=42,
        excerpt="虎杖は魂の入れ替え修行を通じて日下部から簡易領域を習得した。",
        authority="reference",
    )

    store.replace_revision(
        "rev-1",
        entities=[
            KnowledgeEntity(
                entity_id="person:itadori-yuji",
                name="虎杖悠仁",
                entity_type="person",
                aliases=["虎杖", "いたどり"],
                evidence=[evidence],
            )
        ],
        facts=[
            KnowledgeFact(
                fact_id="fact:itadori-simple-domain-learning",
                subject_id="person:itadori-yuji",
                subject="虎杖悠仁",
                predicate="learned_reason",
                object_text="簡易領域",
                value={
                    "reason": "魂の入れ替え修行を通じて日下部から習得した",
                    "teacher": "日下部篤也",
                },
                evidence=[evidence],
                authority="reference",
            )
        ],
    )

    hits = store.search("rev-1", "虎杖が簡易領域を習得できた理由")

    assert hits[0].kind == "fact"
    assert hits[0].record_id == "fact:itadori-simple-domain-learning"
    assert hits[0].data["value"]["teacher"] == "日下部篤也"


def test_structured_store_uses_aliases_for_spelling_variants(tmp_path):
    store = StructuredKnowledgeStore(tmp_path / "structured.sqlite3")
    evidence = EvidenceRef(
        source="source.md",
        chunk_id=12,
        excerpt="禪院家には炳、灯、躯倶留隊などの術師部隊がある。",
    )

    store.replace_revision(
        "rev-1",
        entities=[
            KnowledgeEntity(
                entity_id="org:zenin",
                name="禪院家",
                entity_type="organization",
                aliases=["ぜんいんけ", "禅院家"],
            ),
            KnowledgeEntity(
                entity_id="org:hei",
                name="炳",
                entity_type="organization",
                aliases=["丙", "へい"],
            ),
        ],
        facts=[
            KnowledgeFact(
                fact_id="fact:hei-subgroups",
                subject_id="org:hei",
                subject="炳",
                predicate="has_subgroup",
                object_text="灯、躯倶留隊",
                qualifiers={"parent_organization": "禪院家"},
                evidence=[evidence],
            )
        ],
    )

    hits = store.search("rev-1", "ぜんいんけで丙の下部組織には何があるか")
    context = format_structured_context(hits)

    assert any(hit.record_id == "fact:hei-subgroups" for hit in hits)
    assert "灯、躯倶留隊" in context
    assert "source.md chunk 12" in context


def test_structured_store_can_hold_generic_business_records(tmp_path):
    store = StructuredKnowledgeStore(tmp_path / "structured.sqlite3")

    store.replace_revision(
        "rev-policy",
        entities=[
            KnowledgeEntity(
                entity_id="policy:expense-approval",
                name="経費承認規程",
                entity_type="policy",
                aliases=["経費ルール", "精算規程"],
                attributes={"owner": "finance"},
            )
        ],
        facts=[
            KnowledgeFact(
                fact_id="fact:expense-threshold",
                subject_id="policy:expense-approval",
                subject="経費承認規程",
                predicate="approval_threshold",
                value={"amount": 50000, "currency": "JPY", "approver": "部長"},
            )
        ],
    )

    hits = store.search("rev-policy", "5万円の経費は誰が承認する")

    assert hits[0].record_id == "fact:expense-threshold"
    assert hits[0].data["value"]["approver"] == "部長"


def test_structured_search_prefers_records_covering_more_query_terms(tmp_path):
    store = StructuredKnowledgeStore(tmp_path / "structured.sqlite3")

    store.replace_revision(
        "rev-1",
        entities=[],
        facts=[
            KnowledgeFact(
                fact_id="fact:generic-simple-domain",
                subject="誰でも",
                predicate="学べる",
                object_text="簡易領域",
            ),
            KnowledgeFact(
                fact_id="fact:itadori-learned-simple-domain",
                subject="虎杖悠仁",
                predicate="習得理由",
                object_text="簡易領域",
                value={"reason": "魂の入れ替え修行と日下部の指導"},
            ),
        ],
    )

    hits = store.search("rev-1", "虎杖が簡易領域を習得できた理由")

    assert hits[0].record_id == "fact:itadori-learned-simple-domain"
