"""Build a provenance-preserving knowledge document from a source manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import tomllib
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from src.knowledge_config import PRODUCT_ROOT


DEFAULT_MANIFEST = PRODUCT_ROOT / "config" / "knowledge-sources.toml"
USER_AGENT = "medguide-rag/0.1 local-knowledge-builder"
MAX_RESPONSE_BYTES = 5_000_000
CRAWL_DELAY_SECONDS = 0.1
STATIC_SUFFIXES = {
    ".css", ".gif", ".ico", ".jpeg", ".jpg", ".js", ".json", ".mp3",
    ".mp4", ".pdf", ".png", ".svg", ".webp", ".woff", ".woff2", ".zip",
}


@dataclass(frozen=True)
class SourceSpec:
    id: str
    title: str
    url: str
    strategy: str
    authority: str
    max_pages: int = 1
    include_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class FetchedPage:
    source_id: str
    source_title: str
    source_url: str
    source_type: str
    authority: str
    fetched_at: str
    text: str


@dataclass(frozen=True)
class FetchBatch:
    pages: list[FetchedPage]
    attempted_pages: int
    errors: tuple[str, ...] = ()


class VisibleTextParser(HTMLParser):
    """Extract readable text blocks and links without a browser dependency."""

    BLOCK_TAGS = {
        "address", "article", "blockquote", "br", "dd", "div", "dt", "figcaption",
        "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main",
        "nav", "p", "section", "td", "th", "title",
    }
    SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.current: list[str] = []
        self.blocks: list[str] = []
        self.links: list[str] = []
        self.title = ""
        self._in_title = False

    def _flush(self) -> None:
        value = " ".join(" ".join(self.current).split())
        if value:
            self.blocks.append(value)
        self.current = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self._flush()
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.links.append(str(values["href"]))
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self._flush()
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        self.current.append(value)
        if self._in_title:
            self.title = f"{self.title} {value}".strip()

    def close(self) -> None:
        super().close()
        self._flush()


def _resolve_path(value: str, *, base: Path = PRODUCT_ROOT) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load_source_manifest(
    path: str | Path = DEFAULT_MANIFEST,
) -> tuple[dict[str, Any], list[SourceSpec]]:
    manifest_path = Path(path).resolve()
    data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    build = dict(data.get("build") or {})
    sources = [
        SourceSpec(
            id=str(item["id"]),
            title=str(item["title"]),
            url=str(item["url"]),
            strategy=str(item["strategy"]),
            authority=str(item["authority"]),
            max_pages=max(1, min(int(item.get("max_pages", 1)), 500)),
            include_terms=tuple(str(term) for term in item.get("include_terms", [])),
        )
        for item in data.get("sources", [])
    ]
    if not sources:
        raise ValueError("knowledge source manifest contains no sources")
    ids = [source.id for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("knowledge source IDs must be unique")
    return build, sources


def _request_bytes(url: str, *, accept: str | None = None) -> tuple[bytes, str, str]:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        final_url = response.geturl()
        requested_host = urllib.parse.urlparse(url).netloc.casefold()
        final_host = urllib.parse.urlparse(final_url).netloc.casefold()
        if final_host != requested_host:
            raise ValueError(
                f"cross-host redirect rejected: {requested_host} -> {final_host}"
            )
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")
        return payload, f"{content_type}; charset={charset}", final_url


def _decode_html(raw: bytes, content_type: str) -> tuple[VisibleTextParser, str]:
    charset_match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    charset = charset_match.group(1) if charset_match else "utf-8"
    decoded = raw.decode(charset, errors="replace")
    parser = VisibleTextParser()
    parser.feed(decoded)
    parser.close()
    return parser, decoded


def _fetched_page(spec: SourceSpec, *, url: str, title: str, text: str) -> FetchedPage:
    return FetchedPage(
        source_id=spec.id,
        source_title=title or spec.title,
        source_url=url,
        source_type=spec.strategy,
        authority=spec.authority,
        fetched_at=datetime.now(UTC).isoformat(),
        text=text,
    )


def fetch_wikipedia(spec: SourceSpec) -> list[FetchedPage]:
    title = urllib.parse.unquote(urllib.parse.urlparse(spec.url).path.rsplit("/", 1)[-1])
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "extracts",
            "explaintext": "1",
            "redirects": "1",
            "titles": title,
            "format": "json",
            "formatversion": "2",
        }
    )
    raw, _, _ = _request_bytes(
        f"https://ja.wikipedia.org/w/api.php?{query}",
        accept="application/json",
    )
    payload = json.loads(raw.decode("utf-8"))
    page = payload["query"]["pages"][0]
    text = str(page.get("extract") or "")
    return [_fetched_page(spec, url=spec.url, title=str(page.get("title") or spec.title), text=text)]


def fetch_local(spec: SourceSpec) -> list[FetchedPage]:
    path = _resolve_path(spec.url)
    if not path.is_file() or not path.is_relative_to(PRODUCT_ROOT):
        raise ValueError(f"local knowledge source is unavailable: {spec.url}")
    return [
        _fetched_page(
            spec,
            url=spec.url,
            title=spec.title,
            text=path.read_text(encoding="utf-8"),
        )
    ]


def fetch_web(spec: SourceSpec) -> list[FetchedPage]:
    raw, content_type, final_url = _request_bytes(spec.url, accept="text/html")
    parser, _ = _decode_html(raw, content_type)
    blocks = parser.blocks
    if spec.include_terms:
        blocks = [
            block
            for block in blocks
            if any(term.casefold() in block.casefold() for term in spec.include_terms)
        ]
    return [
        _fetched_page(
            spec,
            url=final_url,
            title=parser.title or spec.title,
            text="\n".join(blocks),
        )
    ]


def _crawl_allowed(url: str, *, host: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    return parsed.scheme in {"http", "https"} and parsed.netloc == host and suffix not in STATIC_SUFFIXES


def _normalize_crawl_url(url: str) -> str:
    parsed = urllib.parse.urlparse(urllib.parse.urldefrag(url)[0])
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path or "/", "", "", "")
    )


def fetch_crawl(spec: SourceSpec) -> FetchBatch:
    host = urllib.parse.urlparse(spec.url).netloc
    queue = deque([_normalize_crawl_url(spec.url)])
    seen: set[str] = set()
    pages: list[FetchedPage] = []
    errors: list[str] = []
    while queue and len(seen) < spec.max_pages:
        url = _normalize_crawl_url(queue.popleft())
        if url in seen or not _crawl_allowed(url, host=host):
            continue
        seen.add(url)
        try:
            raw, content_type, final_url = _request_bytes(url, accept="text/html")
            parser, _ = _decode_html(raw, content_type)
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
            time.sleep(CRAWL_DELAY_SECONDS)
            continue
        pages.append(
            _fetched_page(
                spec,
                url=final_url,
                title=parser.title or spec.title,
                text="\n".join(parser.blocks),
            )
        )
        for href in parser.links:
            candidate = _normalize_crawl_url(urllib.parse.urljoin(final_url, href))
            if candidate not in seen and _crawl_allowed(candidate, host=host):
                queue.append(candidate)
        time.sleep(CRAWL_DELAY_SECONDS)
    return FetchBatch(
        pages=pages,
        attempted_pages=len(seen),
        errors=tuple(errors),
    )


def _walk_reddit_comments(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, dict):
            body = data.get("body") or data.get("selftext") or data.get("title")
            if isinstance(body, str) and body.strip():
                yield body
        for child in value.values():
            yield from _walk_reddit_comments(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_reddit_comments(child)


def fetch_reddit(spec: SourceSpec) -> list[FetchedPage]:
    json_url = f"{spec.url.rstrip('/')}.json?raw_json=1&limit=500"
    raw, _, _ = _request_bytes(json_url, accept="application/json")
    payload = json.loads(raw.decode("utf-8"))
    text = "\n\n".join(dict.fromkeys(_walk_reddit_comments(payload)))
    return [_fetched_page(spec, url=spec.url, title=spec.title, text=text)]


def fetch_youtube(spec: SourceSpec) -> list[FetchedPage]:
    oembed = urllib.parse.urlencode({"url": spec.url, "format": "json"})
    raw, _, _ = _request_bytes(
        f"https://www.youtube.com/oembed?{oembed}",
        accept="application/json",
    )
    payload = json.loads(raw.decode("utf-8"))
    title = str(payload.get("title") or spec.title)
    lines = [
        f"Video title: {title}",
        f"Channel: {payload.get('author_name') or 'unknown'}",
        f"Video URL: {spec.url}",
    ]
    try:
        watch_raw, content_type, _ = _request_bytes(spec.url, accept="text/html")
        _, decoded = _decode_html(watch_raw, content_type)
        match = re.search(r'"shortDescription":"((?:\\.|[^"\\])*)"', decoded)
        if match:
            description = json.loads(f'"{match.group(1)}"')
            if description.strip():
                lines.append(f"Description:\n{description}")
    except Exception:
        pass
    return [_fetched_page(spec, url=spec.url, title=title, text="\n\n".join(lines))]


FETCHERS = {
    "crawl": fetch_crawl,
    "local": fetch_local,
    "reddit": fetch_reddit,
    "web": fetch_web,
    "wikipedia": fetch_wikipedia,
    "youtube": fetch_youtube,
}


def _paragraphs(text: str, *, minimum_characters: int) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    candidates = re.split(r"\n{2,}|\n", normalized)
    paragraphs: list[str] = []
    pending_labels: list[str] = []
    for candidate in candidates:
        value = " ".join(candidate.split())
        if not value:
            continue
        if re.fullmatch(r"={2,}.*={2,}", value) or re.fullmatch(
            r"#{2,6}\s+.+",
            value,
        ):
            pending_labels = []
            paragraphs.append(value)
            continue
        if len(value) < minimum_characters:
            pending_labels.append(value)
            while sum(len(label) for label in pending_labels) > 100:
                pending_labels.pop(0)
            continue
        if pending_labels:
            value = " / ".join([*pending_labels, value])
            pending_labels = []
        paragraphs.append(value)
    return paragraphs


def render_knowledge(
    pages: list[FetchedPage],
    *,
    minimum_paragraph_characters: int = 20,
) -> tuple[str, dict[str, Any]]:
    seen: set[str] = set()
    sections: list[str] = []
    retained_characters = 0
    duplicate_paragraphs = 0
    for page in pages:
        retained: list[str] = []
        for paragraph in _paragraphs(page.text, minimum_characters=minimum_paragraph_characters):
            fingerprint = hashlib.sha256(
                f"{page.source_id}\0{paragraph.casefold()}".encode("utf-8")
            ).hexdigest()
            if fingerprint in seen:
                duplicate_paragraphs += 1
                continue
            seen.add(fingerprint)
            retained.append(paragraph)
        if not retained:
            continue
        metadata = {
            "authority": page.authority,
            "fetched_at": page.fetched_at,
            "source": page.source_title,
            "source_id": page.source_id,
            "source_type": page.source_type,
            "source_url": page.source_url,
        }
        marker = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        body = "\n\n".join(retained)
        retained_characters += len(body)
        sections.append(
            f"<!-- KNOWLEDGE_SOURCE {marker} -->\n"
            f"# {page.source_title}\n\n"
            f"出典URL: {page.source_url}\n\n"
            f"情報区分: {page.authority}\n\n"
            f"{body}"
        )
    rendered = "\n\n".join(sections).strip() + "\n"
    return rendered, {
        "documents": len(sections),
        "retained_characters": retained_characters,
        "duplicate_paragraphs_removed": duplicate_paragraphs,
        "unique_paragraphs": len(seen),
    }


def build_knowledge(
    manifest_path: str | Path = DEFAULT_MANIFEST,
    *,
    output_path: str | Path | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    build, sources = load_source_manifest(manifest_path)
    output = _resolve_path(str(output_path or build["output_path"]))
    report = _resolve_path(str(report_path or build["report_path"]))
    minimum = int(build.get("minimum_paragraph_characters", 20))
    pages: list[FetchedPage] = []
    source_results: list[dict[str, Any]] = []
    for spec in sources:
        result: dict[str, Any] = {**asdict(spec), "status": "failed", "pages": 0, "characters": 0}
        try:
            fetcher = FETCHERS.get(spec.strategy)
            if fetcher is None:
                raise ValueError(f"unsupported fetch strategy: {spec.strategy}")
            fetched_result = fetcher(spec)
            if isinstance(fetched_result, FetchBatch):
                fetched = fetched_result.pages
                result["attempted_pages"] = fetched_result.attempted_pages
                result["failed_pages"] = len(fetched_result.errors)
                result["page_errors"] = list(fetched_result.errors)
            else:
                fetched = fetched_result
            if not fetched or not any(page.text.strip() for page in fetched):
                raise RuntimeError("source returned no usable text")
            pages.extend(fetched)
            result.update(
                status="fetched",
                pages=len(fetched),
                characters=sum(len(page.text) for page in fetched),
            )
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        source_results.append(result)

    fetched_total = sum(item["status"] == "fetched" for item in source_results)
    fetched_external = sum(
        item["status"] == "fetched" and item["strategy"] != "local"
        for item in source_results
    )
    minimum_sources = int(build.get("minimum_sources_fetched", 1))
    minimum_external = int(build.get("minimum_external_sources_fetched", 0))
    if fetched_total < minimum_sources or fetched_external < minimum_external:
        raise RuntimeError(
            "knowledge build source threshold not met: "
            f"fetched={fetched_total}/{minimum_sources}, "
            f"external={fetched_external}/{minimum_external}"
        )

    rendered, metrics = render_knowledge(
        pages,
        minimum_paragraph_characters=minimum,
    )
    if not rendered.strip():
        raise RuntimeError("knowledge build produced no content")
    output_bytes = rendered.encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(output_bytes)
    payload = {
        "built_at": datetime.now(UTC).isoformat(),
        "manifest": str(Path(manifest_path).resolve()),
        "output_path": str(output),
        "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "output_characters": len(rendered),
        "sources_total": len(sources),
        "sources_fetched": sum(item["status"] == "fetched" for item in source_results),
        "sources_failed": sum(item["status"] == "failed" for item in source_results),
        **metrics,
        "sources": source_results,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    result = build_knowledge(args.manifest, output_path=args.output, report_path=args.report)
    print(json.dumps({key: value for key, value in result.items() if key != "sources"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
