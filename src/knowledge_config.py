"""Active knowledge configuration for the RAG demo.

Knowledge-specific values live in config/knowledge.toml, not in the RAG engine.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KNOWLEDGE_CONFIG = PRODUCT_ROOT / "config" / "knowledge.toml"

# Stop terms that are Japanese grammar rather than knowledge-specific vocabulary.
# These stay in code on purpose: a config that omits [lexical] must still tokenize
# Japanese sanely, otherwise swapping the knowledge file would silently turn every
# inflection into a retrieval term.
LANGUAGE_STOP_TERMS = frozenset(
    {"です", "ます", "する", "した", "して", "どんな", "なぜ", "理由", "教えて", "について"}
)

# Interrogatives that name no entity in any domain. Same rationale as above.
LANGUAGE_GENERIC_TERMS = frozenset({"理由", "誰", "何"})


@dataclass(frozen=True)
class IntentProfile:
    """One question intent and the text patterns that answer it.

    `triggers` decide whether a question carries the intent. The rest score a
    candidate chunk once it does, so an intent declared with triggers alone is
    recognised but changes no ranking.
    """

    name: str
    triggers: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()
    paired_markers: tuple[tuple[str, ...], ...] = ()
    proximity_terms: tuple[str, ...] = ()
    demoted_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class LexicalProfile:
    """Knowledge-specific vocabulary used by retrieval, loaded from `[lexical]`.

    Domain words belong here, not in `src/rag`. `stop_terms` and `generic_terms`
    are merged with the language-level defaults above rather than replacing them.
    """

    stop_terms: frozenset[str] = LANGUAGE_STOP_TERMS
    generic_terms: frozenset[str] = LANGUAGE_GENERIC_TERMS
    intents: tuple[IntentProfile, ...] = ()


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
    lexical: LexicalProfile = field(default_factory=LexicalProfile)

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


def _string_tuple(value: Any, *, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{where} は文字列の配列で指定してください")
    return tuple(value)


def _parse_intent(raw: Any, *, index: int) -> IntentProfile:
    where = f"[[lexical.intents]] の {index + 1} 件目"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} はテーブルで指定してください")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError(f"{where} に name がありません")

    paired_raw = raw.get("paired_markers") or []
    if not isinstance(paired_raw, list):
        raise ValueError(f"{where} の paired_markers は配列の配列で指定してください")
    paired = tuple(
        _string_tuple(group, where=f"{where} の paired_markers[{position}]")
        for position, group in enumerate(paired_raw)
    )

    return IntentProfile(
        name=name,
        triggers=_string_tuple(raw.get("triggers"), where=f"{where} の triggers"),
        markers=_string_tuple(raw.get("markers"), where=f"{where} の markers"),
        paired_markers=paired,
        proximity_terms=_string_tuple(raw.get("proximity_terms"), where=f"{where} の proximity_terms"),
        demoted_terms=_string_tuple(raw.get("demoted_terms"), where=f"{where} の demoted_terms"),
    )


def _parse_lexical(data: dict[str, Any], *, config_path: Path) -> LexicalProfile:
    raw = data.get("lexical")
    if raw is None:
        return LexicalProfile()
    if not isinstance(raw, dict):
        raise ValueError(f"[lexical] はテーブルで指定してください: {config_path}")

    intents_raw = raw.get("intents") or []
    if not isinstance(intents_raw, list):
        raise ValueError(f"[[lexical.intents]] は配列で指定してください: {config_path}")

    return LexicalProfile(
        stop_terms=LANGUAGE_STOP_TERMS | set(_string_tuple(raw.get("stop_terms"), where="lexical.stop_terms")),
        generic_terms=LANGUAGE_GENERIC_TERMS
        | set(_string_tuple(raw.get("generic_terms"), where="lexical.generic_terms")),
        intents=tuple(_parse_intent(item, index=index) for index, item in enumerate(intents_raw)),
    )


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
        lexical=_parse_lexical(data, config_path=config_path),
    )


def get_active_knowledge() -> KnowledgeConfig:
    """Return the configured knowledge. Kept as a function for test monkeypatching."""
    return load_knowledge_config()


def active_config_path() -> Path:
    """Resolve which TOML `get_active_knowledge` would read, without reading it."""
    return Path(os.getenv("KNOWLEDGE_CONFIG_PATH") or DEFAULT_KNOWLEDGE_CONFIG).resolve()


@lru_cache(maxsize=8)
def _cached_lexical_profile(config_path: str, mtime_ns: int) -> LexicalProfile:
    return load_knowledge_config(config_path).lexical


def get_lexical_profile() -> LexicalProfile:
    """Return the active `[lexical]` profile, cached per (path, mtime).

    Reranking asks for this once per document, so re-reading the TOML every time
    would put a stat+parse in the retrieval hot loop. Keying the cache on mtime
    keeps edits (and tests that rewrite the file) visible without a manual reset.
    When no config file exists at all, falls back to the language defaults so
    tokenizing stays knowledge-neutral. A config that exists but is malformed
    raises instead of degrading quietly — a silent fallback there would hide the
    very mistake this section makes possible.
    """
    config_path = active_config_path()
    try:
        mtime_ns = config_path.stat().st_mtime_ns
    except OSError:
        return LexicalProfile()
    return _cached_lexical_profile(str(config_path), mtime_ns)
