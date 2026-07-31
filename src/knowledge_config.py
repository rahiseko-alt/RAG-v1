"""Active knowledge configuration for the RAG demo.

Knowledge-specific values live in config/knowledge.toml, not in the RAG engine.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KNOWLEDGE_CONFIG = PRODUCT_ROOT / "config" / "knowledge.toml"


@dataclass(frozen=True)
class KnowledgeConfig:
    id: str
    title: str
    description: str
    source_path: Path
    collection: str
    source_url: str
    license: str
    checked_at: str
    eval_set: Path | None
    example_question: str
    expected_terms: tuple[str, ...]
    config_path: Path

    def public_dict(self) -> dict[str, Any]:
        """Return metadata safe to expose from the local API."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "source": self.source_path.name,
            "source_path": str(self.source_path.relative_to(PRODUCT_ROOT))
            if self.source_path.is_relative_to(PRODUCT_ROOT)
            else str(self.source_path),
            "collection": self.collection,
            "source_url": self.source_url,
            "license": self.license,
            "checked_at": self.checked_at,
            "eval_set": str(self.eval_set.relative_to(PRODUCT_ROOT))
            if self.eval_set and self.eval_set.is_relative_to(PRODUCT_ROOT)
            else (str(self.eval_set) if self.eval_set else None),
            "example_question": self.example_question,
            "config_path": str(self.config_path.relative_to(PRODUCT_ROOT))
            if self.config_path.is_relative_to(PRODUCT_ROOT)
            else str(self.config_path),
        }


def _resolve_path(value: str | None, *, base: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load_knowledge_config(path: str | Path | None = None) -> KnowledgeConfig:
    """Load the active knowledge config.

    KNOWLEDGE_CONFIG_PATH can point to another TOML file for client/project-specific
    knowledge without changing application code.
    """
    config_path = Path(path or os.getenv("KNOWLEDGE_CONFIG_PATH") or DEFAULT_KNOWLEDGE_CONFIG).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"ナレッジ設定ファイルが見つかりません: {config_path}")

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    raw = data.get("knowledge")
    if not isinstance(raw, dict):
        raise ValueError(f"[knowledge] セクションがありません: {config_path}")

    source_path = _resolve_path(raw.get("source_path"), base=PRODUCT_ROOT)
    if source_path is None:
        raise ValueError(f"knowledge.source_path が未設定です: {config_path}")
    if not source_path.exists():
        raise FileNotFoundError(f"ナレッジ本文が見つかりません: {source_path}")

    eval_set = _resolve_path(raw.get("eval_set"), base=PRODUCT_ROOT)
    expected_terms = raw.get("expected_terms", [])
    if isinstance(expected_terms, str):
        expected_terms = [expected_terms]

    collection = str(raw.get("collection") or f"knowledge_{source_path.stem}")
    return KnowledgeConfig(
        id=str(raw.get("id") or source_path.stem),
        title=str(raw.get("title") or source_path.stem),
        description=str(raw.get("description") or ""),
        source_path=source_path,
        collection=collection,
        source_url=str(raw.get("source_url") or ""),
        license=str(raw.get("license") or ""),
        checked_at=str(raw.get("checked_at") or ""),
        eval_set=eval_set,
        example_question=str(raw.get("example_question") or ""),
        expected_terms=tuple(str(term) for term in expected_terms),
        config_path=config_path,
    )


def get_active_knowledge() -> KnowledgeConfig:
    """Return the configured knowledge. Kept as a function for test monkeypatching."""
    return load_knowledge_config()
