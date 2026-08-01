"""Extract domain-neutral structured knowledge records from text chunks."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Protocol

from langchain_core.documents import Document
from pydantic import BaseModel, Field

from src.rag import build_chat_model, get_generation_model
from src.structured_knowledge import EvidenceRef, KnowledgeEntity, KnowledgeFact


MAX_EXTRACTION_CHARS = 3500


class ExtractedKnowledgeBatch(BaseModel):
    """Structured records extracted from one source chunk."""

    entities: list[KnowledgeEntity] = Field(default_factory=list)
    facts: list[KnowledgeFact] = Field(default_factory=list)


class StructuredExtractor(Protocol):
    """Small protocol so tests and future extractors can be swapped in."""

    def extract_document(self, document: Document) -> ExtractedKnowledgeBatch:
        """Extract structured records from one document chunk."""


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _model_copy(model: BaseModel, update: dict[str, Any]) -> BaseModel:
    if hasattr(model, "model_copy"):
        return model.model_copy(update=update)
    return model.copy(update=update)


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _fallback_evidence(document: Document) -> EvidenceRef:
    metadata = document.metadata or {}
    excerpt = " ".join(str(document.page_content).split())[:500]
    return EvidenceRef(
        source=str(metadata.get("source") or "unknown"),
        chunk_id=metadata.get("chunk_id") if isinstance(metadata.get("chunk_id"), int) else None,
        page=metadata.get("page"),
        source_id=metadata.get("source_id"),
        source_url=metadata.get("source_url"),
        authority=str(metadata.get("authority") or "unknown"),
        excerpt=excerpt,
    )


def _normalize_evidence_payload(payload: dict[str, Any], document: Document) -> dict[str, Any]:
    """Coerce LLM evidence shortcuts into EvidenceRef-compatible dictionaries."""

    fallback = _model_dump(_fallback_evidence(document))
    normalized = dict(payload)
    for collection_name in ("entities", "facts"):
        records = normalized.get(collection_name)
        if not isinstance(records, list):
            normalized[collection_name] = []
            continue
        converted_records: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            converted = dict(record)
            evidence_items = converted.get("evidence") or []
            if not isinstance(evidence_items, list):
                evidence_items = [evidence_items]
            converted_evidence: list[dict[str, Any]] = []
            for item in evidence_items:
                if isinstance(item, dict):
                    ref = {**fallback, **item}
                    if not ref.get("source"):
                        ref["source"] = fallback["source"]
                    converted_evidence.append(ref)
                elif isinstance(item, str) and item.strip():
                    converted_evidence.append({**fallback, "excerpt": item.strip()})
            converted["evidence"] = converted_evidence
            converted_records.append(converted)
        normalized[collection_name] = converted_records
    return normalized


def _ensure_record_ids(batch: ExtractedKnowledgeBatch, document: Document) -> ExtractedKnowledgeBatch:
    """Fill missing IDs/evidence so records can be passed between systems safely."""

    fallback = _fallback_evidence(document)
    entities: list[KnowledgeEntity] = []
    facts: list[KnowledgeFact] = []
    for entity in batch.entities:
        evidence = entity.evidence or [fallback]
        entity_id = entity.entity_id or _stable_id(
            "entity",
            {
                "name": entity.name,
                "entity_type": entity.entity_type,
                "aliases": entity.aliases,
            },
        )
        entities.append(
            _model_copy(entity, {"entity_id": entity_id, "evidence": evidence})
        )
    for fact in batch.facts:
        if "不明" in fact.subject and not fact.subject_id:
            continue
        evidence = fact.evidence or [fallback]
        fact_id = fact.fact_id or _stable_id(
            "fact",
            {
                "subject": fact.subject,
                "predicate": fact.predicate,
                "object_text": fact.object_text,
                "value": fact.value,
                "qualifiers": fact.qualifiers,
            },
        )
        facts.append(
            _model_copy(fact, {"fact_id": fact_id, "evidence": evidence})
        )
    return ExtractedKnowledgeBatch(entities=entities, facts=facts)


def merge_extracted_batches(batches: list[ExtractedKnowledgeBatch]) -> ExtractedKnowledgeBatch:
    """Deduplicate records from many chunks while preserving first evidence."""

    entities: dict[str, KnowledgeEntity] = {}
    facts: dict[str, KnowledgeFact] = {}
    for batch in batches:
        for entity in batch.entities:
            if entity.entity_id not in entities:
                entities[entity.entity_id] = entity
        for fact in batch.facts:
            if fact.fact_id not in facts:
                facts[fact.fact_id] = fact
    return ExtractedKnowledgeBatch(
        entities=list(entities.values()),
        facts=list(facts.values()),
    )


class LLMStructuredExtractor:
    """Use a generation model to convert natural language chunks into records."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or get_generation_model()
        self._model = build_chat_model(self.model)

    @staticmethod
    def _parse_json_response(value: Any) -> dict[str, Any]:
        content = value.content if hasattr(value, "content") else value
        text = content if isinstance(content, str) else str(content)
        text = text.strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("structured extraction response must be a JSON object")
        return payload

    def extract_document(self, document: Document) -> ExtractedKnowledgeBatch:
        metadata = document.metadata or {}
        text = str(document.page_content).strip()[:MAX_EXTRACTION_CHARS]
        prompt = (
            "次の文書チャンクから、どの業務領域にも流用できる構造化ナレッジを抽出してください。\n"
            "固有ドメイン専用の列は作らず、entity と fact だけで表現してください。\n"
            "entity は人物・組織・製品・薬剤・制度・規程・概念などの名前付き対象です。\n"
            "fact は subject / predicate / object_text または value で表す1つの事実です。\n"
            "本文に明示されない推測は抽出しないでください。\n"
            "IDは空で構いません。後段で安定IDを補完します。\n\n"
            "JSONだけを返してください。形式は次です。\n"
            "{\n"
            '  "entities": [\n'
            '    {"entity_id": "", "name": "対象名", "entity_type": "person|organization|policy|concept|unknown", "aliases": [], "attributes": {}, "evidence": []}\n'
            "  ],\n"
            '  "facts": [\n'
            '    {"fact_id": "", "subject_id": null, "subject": "主語", "predicate": "関係名", "object_id": null, "object_text": "目的語または説明", "value": null, "qualifiers": {}, "evidence": [], "confidence": "asserted", "authority": "unknown"}\n'
            "  ]\n"
            "}\n\n"
            f"metadata: {json.dumps(metadata, ensure_ascii=False, sort_keys=True)}\n\n"
            f"text:\n{text}"
        )
        raw = self._model.invoke(prompt)
        payload = _normalize_evidence_payload(self._parse_json_response(raw), document)
        batch = ExtractedKnowledgeBatch.model_validate(payload)
        return _ensure_record_ids(batch, document)


def extract_structured_records(
    documents: list[Document],
    *,
    extractor: StructuredExtractor,
    limit: int | None = None,
) -> ExtractedKnowledgeBatch:
    """Extract and merge structured records from a bounded list of chunks."""

    selected = documents[:limit] if limit is not None else documents
    batches = [
        _ensure_record_ids(extractor.extract_document(document), document)
        for document in selected
    ]
    return merge_extracted_batches(batches)
