"""Domain-neutral structured knowledge records for retrieval augmentation.

This module deliberately avoids anime, medical, or business-specific fields.
Any domain can be represented as:

- entities: named things with aliases and typed attributes
- facts: subject-predicate-object/value statements
- evidence: source/chunk references that justify the record
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field

from src.knowledge_config import PRODUCT_ROOT


DEFAULT_STRUCTURED_DB_PATH = PRODUCT_ROOT / "data" / "runtime" / "structured_knowledge.sqlite3"
RecordKind = Literal["entity", "fact"]


class EvidenceRef(BaseModel):
    """Source location that supports a structured record."""

    source: str
    chunk_id: int | None = None
    page: str | int | None = None
    source_id: str | None = None
    source_url: str | None = None
    excerpt: str = ""
    authority: str = "unknown"


class KnowledgeEntity(BaseModel):
    """A named object in any knowledge domain."""

    entity_id: str
    name: str
    entity_type: str = "unknown"
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class KnowledgeFact(BaseModel):
    """A subject-predicate-object/value statement with source evidence."""

    fact_id: str
    subject_id: str | None = None
    subject: str
    predicate: str
    object_id: str | None = None
    object_text: str | None = None
    value: Any = None
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: str = "asserted"
    authority: str = "unknown"


class StructuredSearchHit(BaseModel):
    """A structured record matched by query terms."""

    kind: RecordKind
    record_id: str
    score: float
    title: str
    text: str
    data: dict[str, Any]


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


def _expand_iteration_marks(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char == "々" and chars:
            chars.append(chars[-1])
        else:
            chars.append(char)
    return "".join(chars)


def _normalize_text(value: str) -> str:
    return _expand_iteration_marks(value).casefold()


def _query_terms(query: str) -> list[str]:
    normalized = _normalize_text(query)
    pieces = re.split(
        r"[\s、。！？!?・/／（）()「」『』【】\[\]:：,，"
        r"のはがをにへでとやもか]+",
        normalized,
    )
    stop_terms = {
        "です",
        "ます",
        "する",
        "した",
        "して",
        "どんな",
        "なぜ",
        "理由",
        "教えて",
        "について",
        "答えろ",
        "こたえろ",
        "誰",
        "何",
        "ある",
    }
    terms: list[str] = []
    for piece in pieces:
        term = piece.strip()
        if len(term) < 2 or term in stop_terms:
            continue
        if term not in terms:
            terms.append(term)
        for suffix in ("できた理由", "できる理由", "した理由", "の理由"):
            if term.endswith(suffix):
                base = term[: -len(suffix)].strip()
                if len(base) >= 2 and base not in terms:
                    terms.append(base)
        if "習得" in term and "習得" not in terms:
            terms.append("習得")
        if "下部組織" in term and "下部組織" not in terms:
            terms.append("下部組織")
    return terms


def _entity_content(entity: KnowledgeEntity) -> str:
    return "\n".join(
        [
            entity.name,
            entity.entity_type,
            " ".join(entity.aliases),
            _json(entity.attributes),
            "\n".join(ref.excerpt for ref in entity.evidence if ref.excerpt),
        ]
    )


def _fact_content(fact: KnowledgeFact) -> str:
    return "\n".join(
        [
            fact.subject,
            fact.predicate,
            fact.object_text or "",
            _json(fact.value),
            _json(fact.qualifiers),
            "\n".join(ref.excerpt for ref in fact.evidence if ref.excerpt),
        ]
    )


def _score_content(query: str, content: str, aliases: list[str] | None = None) -> float:
    normalized_query = _normalize_text(query)
    normalized_content = _normalize_text(content)
    score = 0.0
    if normalized_query and normalized_query in normalized_content:
        score += 20.0
    for alias in aliases or []:
        normalized_alias = _normalize_text(alias)
        if normalized_alias and normalized_alias in normalized_query:
            score += 12.0
    terms = _query_terms(query)
    matched_terms = []
    for term in terms:
        if term in normalized_content:
            matched_terms.append(term)
            score += 4.0 + min(3.0, normalized_content.count(term))
    if terms:
        coverage = len(set(matched_terms)) / len(set(terms))
        score += coverage * 10.0
        if len(set(matched_terms)) <= 1 and len(set(terms)) >= 3:
            score -= 6.0
    return score


class StructuredKnowledgeStore:
    """SQLite-backed structured knowledge cache.

    Chroma remains the evidence store for full text chunks. This store holds
    compact typed records that are easier to filter, compare, and pass between
    systems.
    """

    def __init__(self, db_path: str | Path = DEFAULT_STRUCTURED_DB_PATH) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _create_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS structured_entities (
                    revision_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    PRIMARY KEY (revision_id, entity_id)
                );

                CREATE TABLE IF NOT EXISTS structured_facts (
                    revision_id TEXT NOT NULL,
                    fact_id TEXT NOT NULL,
                    subject_id TEXT,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_id TEXT,
                    object_text TEXT,
                    value_json TEXT NOT NULL,
                    qualifiers_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    authority TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    PRIMARY KEY (revision_id, fact_id)
                );

                CREATE INDEX IF NOT EXISTS idx_structured_entities_type
                    ON structured_entities(revision_id, entity_type);
                CREATE INDEX IF NOT EXISTS idx_structured_facts_subject
                    ON structured_facts(revision_id, subject_id, subject);
                CREATE INDEX IF NOT EXISTS idx_structured_facts_predicate
                    ON structured_facts(revision_id, predicate);
                """
            )

    def replace_revision(
        self,
        revision_id: str,
        *,
        entities: list[KnowledgeEntity],
        facts: list[KnowledgeFact],
    ) -> None:
        """Replace the structured cache for one immutable source revision."""

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM structured_entities WHERE revision_id = ?",
                (revision_id,),
            )
            connection.execute(
                "DELETE FROM structured_facts WHERE revision_id = ?",
                (revision_id,),
            )
            connection.executemany(
                """
                INSERT INTO structured_entities(
                    revision_id, entity_id, name, entity_type, aliases_json,
                    attributes_json, evidence_json, search_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        revision_id,
                        entity.entity_id,
                        entity.name,
                        entity.entity_type,
                        _json(entity.aliases),
                        _json(entity.attributes),
                        _json([_model_dump(ref) for ref in entity.evidence]),
                        _entity_content(entity),
                    )
                    for entity in entities
                ],
            )
            connection.executemany(
                """
                INSERT INTO structured_facts(
                    revision_id, fact_id, subject_id, subject, predicate,
                    object_id, object_text, value_json, qualifiers_json,
                    evidence_json, confidence, authority, search_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        revision_id,
                        fact.fact_id,
                        fact.subject_id,
                        fact.subject,
                        fact.predicate,
                        fact.object_id,
                        fact.object_text,
                        _json(fact.value),
                        _json(fact.qualifiers),
                        _json([_model_dump(ref) for ref in fact.evidence]),
                        fact.confidence,
                        fact.authority,
                        _fact_content(fact),
                    )
                    for fact in facts
                ],
            )

    def search(self, revision_id: str, query: str, *, limit: int = 8) -> list[StructuredSearchHit]:
        """Return structured records relevant to a natural-language query."""

        hits: list[StructuredSearchHit] = []
        with self._connect() as connection:
            entities = connection.execute(
                """
                SELECT * FROM structured_entities
                WHERE revision_id = ?
                """,
                (revision_id,),
            ).fetchall()
            facts = connection.execute(
                """
                SELECT * FROM structured_facts
                WHERE revision_id = ?
                """,
                (revision_id,),
            ).fetchall()

        alias_by_id: dict[str, list[str]] = {}
        alias_by_name: dict[str, list[str]] = {}
        for row in entities:
            aliases = _decode(row["aliases_json"], [])
            values = [row["name"], *aliases]
            alias_by_id[row["entity_id"]] = values
            alias_by_name[row["name"]] = values

        for row in entities:
            aliases = _decode(row["aliases_json"], [])
            score = _score_content(query, row["search_text"], aliases)
            if score <= 0:
                continue
            data = {
                "entity_id": row["entity_id"],
                "name": row["name"],
                "entity_type": row["entity_type"],
                "aliases": aliases,
                "attributes": _decode(row["attributes_json"], {}),
                "evidence": _decode(row["evidence_json"], []),
            }
            hits.append(
                StructuredSearchHit(
                    kind="entity",
                    record_id=row["entity_id"],
                    score=score,
                    title=f"{row['name']} ({row['entity_type']})",
                    text=row["search_text"],
                    data=data,
                )
            )

        for row in facts:
            content = row["search_text"]
            fact_aliases: list[str] = []
            if row["subject_id"] and row["subject_id"] in alias_by_id:
                fact_aliases.extend(alias_by_id[row["subject_id"]])
            if row["object_id"] and row["object_id"] in alias_by_id:
                fact_aliases.extend(alias_by_id[row["object_id"]])
            for name, aliases in alias_by_name.items():
                if name in content:
                    fact_aliases.extend(aliases)
            score = _score_content(query, content, fact_aliases)
            if score <= 0:
                continue
            score += 5.0
            object_value = row["object_text"] or _decode(row["value_json"], None)
            title = f"{row['subject']} - {row['predicate']}"
            if object_value is not None and object_value != "":
                rendered = object_value if isinstance(object_value, str) else _json(object_value)
                title += f" - {rendered}"
            data = {
                "fact_id": row["fact_id"],
                "subject_id": row["subject_id"],
                "subject": row["subject"],
                "predicate": row["predicate"],
                "object_id": row["object_id"],
                "object_text": row["object_text"],
                "value": _decode(row["value_json"], None),
                "qualifiers": _decode(row["qualifiers_json"], {}),
                "evidence": _decode(row["evidence_json"], []),
                "confidence": row["confidence"],
                "authority": row["authority"],
            }
            hits.append(
                StructuredSearchHit(
                    kind="fact",
                    record_id=row["fact_id"],
                    score=score,
                    title=title,
                    text=content,
                    data=data,
                )
            )

        return sorted(hits, key=lambda hit: (-hit.score, hit.kind, hit.record_id))[:limit]


def format_structured_context(hits: list[StructuredSearchHit]) -> str:
    """Render structured hits as compact context for a downstream LLM."""

    blocks: list[str] = []
    for index, hit in enumerate(hits, start=1):
        evidence = hit.data.get("evidence") or []
        refs = []
        for ref in evidence:
            source = ref.get("source", "?")
            chunk = ref.get("chunk_id")
            refs.append(f"{source} chunk {chunk}" if chunk is not None else source)
        blocks.append(
            f"[S{index}] kind={hit.kind}; id={hit.record_id}; score={hit.score:.1f}\n"
            f"title: {hit.title}\n"
            f"data: {_json(hit.data)}\n"
            f"evidence: {', '.join(refs) if refs else 'none'}"
        )
    return "\n\n".join(blocks)
