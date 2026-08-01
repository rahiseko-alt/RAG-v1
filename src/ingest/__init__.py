"""src/ingest — ナレッジ文書を読み込み、検索しやすい単位に分割する。

Stage 3。入力は `data/sample/` 配下の PDF / Markdown / Text、出力は metadata（source / page）付きの
チャンク化 Document 列で、`src/rag` の埋め込み処理へ渡す。

チャンク戦略（architecture.md の未決事項をここで確定）:
  RecursiveCharacterTextSplitter を用い、段落 → 行 → 文 → 語の順で「意味の切れ目」を
  優先して chunk_size≈500 文字・overlap≈75 文字に分割する。見出し単位だと章の長さの
  ばらつきが大きく検索粒度が不均一になるため、固定サイズ＋境界優先の標準手法を採る。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

# チャンク分割の既定値（意味の切れ目を優先しつつ、埋め込みに載る長さに収める）
CHUNK_SIZE = 500
CHUNK_OVERLAP = 75
MAX_PDF_PAGES = 200
MAX_EXTRACTED_CHARACTERS = 5_000_000
KNOWLEDGE_SOURCE_MARKER = re.compile(
    r"^<!-- KNOWLEDGE_SOURCE (?P<metadata>\{.*\}) -->\s*$",
    re.MULTILINE,
)
MARKDOWN_SECTION_HEADING = re.compile(r"^#{2,6}\s+.+$", re.MULTILINE)


def source_sha256(source_path: str | Path) -> str:
    """Return a content fingerprint for index freshness checks."""
    source_path = Path(source_path)
    return hashlib.sha256(source_path.read_bytes()).hexdigest()


def load_pdf(pdf_path: str | Path) -> list[Document]:
    """PDF をページ単位の Document 列として読み込む。

    各 Document の metadata には source（ファイル名）と page（0始まりのページ番号）が入る。
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF が見つかりません: {pdf_path}")
    if not pdf_path.read_bytes()[:5].startswith(b"%PDF-"):
        raise ValueError("PDF header is invalid")
    reader = PdfReader(str(pdf_path))
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"PDF exceeds the {MAX_PDF_PAGES}-page local-workbench limit")
    pages: list[Document] = []
    extracted_characters = 0
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        extracted_characters += len(text)
        if extracted_characters > MAX_EXTRACTED_CHARACTERS:
            raise ValueError("PDF extracted text exceeds the local-workbench limit")
        # metadata: source=ファイル名（可搬性）, page=0始まりのページ番号
        pages.append(Document(page_content=text, metadata={"source": pdf_path.name, "page": i}))
    return pages


def load_text(text_path: str | Path) -> list[Document]:
    """Markdown / Text を Document として読み込む。"""
    text_path = Path(text_path)
    if not text_path.exists():
        raise FileNotFoundError(f"テキスト文書が見つかりません: {text_path}")
    text = text_path.read_text(encoding="utf-8")
    markers = list(KNOWLEDGE_SOURCE_MARKER.finditer(text))
    if markers:
        documents: list[Document] = []
        for index, marker in enumerate(markers):
            start = marker.end()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            content = text[start:end].strip()
            if not content:
                continue
            metadata = json.loads(marker.group("metadata"))
            safe_metadata = {
                str(key): value
                for key, value in metadata.items()
                if isinstance(value, (str, int, float, bool))
            }
            safe_metadata.setdefault("source", text_path.name)
            headings = list(MARKDOWN_SECTION_HEADING.finditer(content))
            starts = [0, *(match.start() for match in headings)]
            starts = sorted(set(starts))
            for section_index, section_start in enumerate(starts):
                section_end = (
                    starts[section_index + 1]
                    if section_index + 1 < len(starts)
                    else len(content)
                )
                section = content[section_start:section_end].strip()
                if not section:
                    continue
                section_metadata = dict(safe_metadata)
                section_metadata["page"] = f"{index}:{section_index}"
                documents.append(
                    Document(page_content=section, metadata=section_metadata)
                )
        return documents
    return [Document(page_content=text, metadata={"source": text_path.name, "page": 0})]


def load_documents(source_path: str | Path) -> list[Document]:
    """拡張子に応じて PDF / Markdown / Text を読み込む。"""
    source_path = Path(source_path)
    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        return load_pdf(source_path)
    if suffix in {".md", ".txt"}:
        return load_text(source_path)
    raise ValueError(f"未対応のナレッジ形式です: {source_path}")


def chunk_documents(
    docs: list[Document],
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> list[Document]:
    """ページ Document 列を、意味の切れ目優先で小さなチャンクに分割する。

    metadata（source / page）はチャンクに引き継がれ、後段の出典追跡に使う。
    さらに chunk_id（連番）を各チャンクに付与する。
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", ". ", " ", ""],  # 段落→行→文→語→文字の順
    )
    chunks = splitter.split_documents(docs)
    # 空白のみ・極端に短いチャンク（ページ番号だけ等）を除外
    chunks = [c for c in chunks if len(c.page_content.strip()) >= 30]
    for i, c in enumerate(chunks):
        c.metadata["chunk_id"] = i
    return chunks


def load_and_chunk(source_path: str | Path) -> list[Document]:
    """ナレッジ読み込み → チャンク分割 をまとめて実行する便利関数。"""
    return chunk_documents(load_documents(source_path))
