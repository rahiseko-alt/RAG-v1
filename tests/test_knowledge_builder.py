import json
from pathlib import Path

import pytest

from src.ingest import chunk_documents, load_text
from src.knowledge_builder import (
    FETCHERS,
    FetchedPage,
    SourceSpec,
    _request_bytes,
    build_knowledge,
    fetch_crawl,
    load_source_manifest,
    render_knowledge,
)


def test_manifest_contains_only_unique_source_ids():
    _build, sources = load_source_manifest()

    assert len(sources) >= 20
    assert len({source.id for source in sources}) == len(sources)
    assert {"official", "reference", "fan"} <= {source.authority for source in sources}
    assert any(source.strategy == "local" for source in sources)


def test_rendered_knowledge_preserves_source_metadata(tmp_path: Path):
    pages = [
        FetchedPage(
            source_id="official-example",
            source_title="公式資料",
            source_url="https://example.com/official",
            source_type="web",
            authority="official",
            fetched_at="2026-07-31T00:00:00+00:00",
            text="術式についての十分に長い説明です。検索と回答の根拠として利用します。",
        ),
        FetchedPage(
            source_id="fan-example",
            source_title="ファン議論",
            source_url="https://example.com/fan",
            source_type="reddit",
            authority="fan",
            fetched_at="2026-07-31T00:00:00+00:00",
            text="この説明はファンによる解釈であり、公式確定情報とは区別して扱います。",
        ),
    ]
    rendered, metrics = render_knowledge(pages)
    target = tmp_path / "generated.md"
    target.write_text(rendered, encoding="utf-8")

    documents = load_text(target)

    assert metrics["documents"] == 2
    assert len(documents) == 2
    assert documents[0].metadata["source_url"] == "https://example.com/official"
    assert documents[0].metadata["authority"] == "official"
    assert documents[1].metadata["authority"] == "fan"


def test_duplicate_paragraphs_keep_distinct_source_provenance():
    duplicate = "同一内容が複数の取得元に存在する場合は一度だけナレッジへ残します。"
    pages = [
        FetchedPage("a", "A", "https://a.example", "web", "official", "now", duplicate),
        FetchedPage("b", "B", "https://b.example", "web", "reference", "now", duplicate),
    ]

    rendered, metrics = render_knowledge(pages)

    assert rendered.count(duplicate) == 2
    assert "https://a.example" in rendered
    assert "https://b.example" in rendered
    assert metrics["duplicate_paragraphs_removed"] == 0


def test_duplicate_paragraphs_are_removed_within_one_source():
    duplicate = "同じ取得元で重複する内容は一度だけナレッジへ残します。"
    pages = [
        FetchedPage("a", "A1", "https://a.example/1", "web", "official", "now", duplicate),
        FetchedPage("a", "A2", "https://a.example/2", "web", "official", "now", duplicate),
    ]

    rendered, metrics = render_knowledge(pages)

    assert rendered.count(duplicate) == 1
    assert metrics["duplicate_paragraphs_removed"] == 1


def test_short_entity_label_is_attached_to_following_description():
    pages = [
        FetchedPage(
            "a",
            "A",
            "https://a.example",
            "wikipedia",
            "reference",
            "now",
            "東堂 葵\n術式「不義遊戯」の使い手で、対象の位置を入れ替える能力を持つ。",
        )
    ]

    rendered, _metrics = render_knowledge(pages)

    assert "東堂 葵 / 術式「不義遊戯」" in rendered


def test_generated_documents_chunk_without_losing_provenance(tmp_path: Path):
    metadata = json.dumps(
        {
            "source": "公式資料",
            "source_url": "https://example.com",
            "source_type": "web",
            "authority": "official",
        },
        ensure_ascii=False,
    )
    content = (
        f"<!-- KNOWLEDGE_SOURCE {metadata} -->\n"
        "# 公式資料\n\n"
        + "これは検索対象となる公式情報です。" * 80
    )
    target = tmp_path / "generated.md"
    target.write_text(content, encoding="utf-8")

    chunks = chunk_documents(load_text(target))

    assert len(chunks) >= 3
    assert all(chunk.metadata["source_url"] == "https://example.com" for chunk in chunks)
    assert all(chunk.metadata["authority"] == "official" for chunk in chunks)


def test_generated_markdown_headings_create_search_boundaries(tmp_path: Path):
    metadata = json.dumps(
        {
            "source": "運用方針",
            "source_url": "https://example.com/policy",
            "source_type": "local",
            "authority": "curated",
        },
        ensure_ascii=False,
    )
    target = tmp_path / "generated.md"
    target.write_text(
        f"""<!-- KNOWLEDGE_SOURCE {metadata} -->
# 運用方針

## SNS管理

SNS管理についての十分に長い説明をここへ記載します。

## YouTube管理

YouTube管理についての十分に長い説明をここへ記載します。
""",
        encoding="utf-8",
    )

    documents = load_text(target)

    assert len(documents) == 3
    assert "SNS管理" in documents[1].page_content
    assert "YouTube管理" not in documents[1].page_content
    assert len({document.metadata["page"] for document in documents}) == 3


def test_build_report_hash_matches_written_file(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "sources.toml"
    output = tmp_path / "knowledge.md"
    report = tmp_path / "report.json"
    manifest.write_text(
        f"""
[build]
output_path = {json.dumps(str(output))}
report_path = {json.dumps(str(report))}

[[sources]]
id = "example"
title = "Example"
url = "https://example.com"
strategy = "web"
authority = "official"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setitem(
        FETCHERS,
        "web",
        lambda spec: [
            FetchedPage(
                spec.id,
                spec.title,
                spec.url,
                spec.strategy,
                spec.authority,
                "2026-07-31T00:00:00+00:00",
                "改行を含む十分に長い検証用テキストです。\n別の段落も保存します。",
            )
        ],
    )

    result = build_knowledge(manifest)

    import hashlib

    assert result["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_cross_host_redirect_is_rejected(monkeypatch):
    class Headers:
        @staticmethod
        def get_content_type():
            return "text/html"

        @staticmethod
        def get_content_charset():
            return "utf-8"

    class Response:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def geturl():
            return "https://attacker.example/payload"

        @staticmethod
        def read(_limit):
            return b"payload"

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(ValueError, match="cross-host redirect rejected"):
        _request_bytes("https://official.example/source")


def test_build_fails_when_source_threshold_is_not_met(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "sources.toml"
    output = tmp_path / "knowledge.md"
    report = tmp_path / "report.json"
    manifest.write_text(
        f"""
[build]
output_path = {json.dumps(str(output))}
report_path = {json.dumps(str(report))}
minimum_sources_fetched = 1
minimum_external_sources_fetched = 1

[[sources]]
id = "unavailable"
title = "Unavailable"
url = "https://example.com"
strategy = "web"
authority = "official"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setitem(FETCHERS, "web", lambda _spec: [])

    with pytest.raises(RuntimeError, match="source threshold not met"):
        build_knowledge(manifest)

    assert not output.exists()


def test_crawl_reports_failed_child_without_discarding_root(monkeypatch):
    root_html = (
        b"<html><title>Root</title><body><p>"
        + "十分に長い本文です。".encode("utf-8") * 4
        + b'</p><a href="/broken">broken</a></body></html>'
    )

    def fake_request(url, **_kwargs):
        if url.endswith("/broken"):
            raise TimeoutError("child timed out")
        return root_html, "text/html; charset=utf-8", url

    monkeypatch.setattr("src.knowledge_builder._request_bytes", fake_request)
    monkeypatch.setattr("src.knowledge_builder.time.sleep", lambda _seconds: None)
    spec = SourceSpec(
        id="crawl",
        title="Crawl",
        url="https://example.com/",
        strategy="crawl",
        authority="official",
        max_pages=2,
    )

    result = fetch_crawl(spec)

    assert len(result.pages) == 1
    assert result.attempted_pages == 2
    assert len(result.errors) == 1
    assert "child timed out" in result.errors[0]


def test_crawl_deduplicates_query_variants(monkeypatch):
    root_html = (
        b"<html><title>Root</title><body><p>"
        + "十分に長い本文です。".encode("utf-8") * 4
        + b'</p><a href="/?page=1">one</a><a href="/?page=2">two</a></body></html>'
    )
    calls = []

    def fake_request(url, **_kwargs):
        calls.append(url)
        return root_html, "text/html; charset=utf-8", url

    monkeypatch.setattr("src.knowledge_builder._request_bytes", fake_request)
    monkeypatch.setattr("src.knowledge_builder.time.sleep", lambda _seconds: None)
    spec = SourceSpec(
        id="crawl",
        title="Crawl",
        url="https://example.com/",
        strategy="crawl",
        authority="official",
        max_pages=250,
    )

    result = fetch_crawl(spec)

    assert calls == ["https://example.com/"]
    assert result.attempted_pages == 1
