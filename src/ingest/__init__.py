"""src/ingest — 医療ガイドライン文書（PDF）を読み込み、検索しやすい単位に分割する。

Stage 3。入力は `data/sample/` 配下の PDF、出力は metadata（source / page）付きの
チャンク化 Document 列で、`src/rag` の埋め込み処理へ渡す。

チャンク戦略（architecture.md の未決事項をここで確定）:
  RecursiveCharacterTextSplitter を用い、段落 → 行 → 文 → 語の順で「意味の切れ目」を
  優先して chunk_size≈1000 文字・overlap≈150 文字に分割する。見出し単位だと章の長さの
  ばらつきが大きく検索粒度が不均一になるため、固定サイズ＋境界優先の標準手法を採る。
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

# チャンク分割の既定値（意味の切れ目を優先しつつ、埋め込みに載る長さに収める）
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def load_pdf(pdf_path: str | Path) -> list[Document]:
    """PDF をページ単位の Document 列として読み込む。

    各 Document の metadata には source（ファイル名）と page（0始まりのページ番号）が入る。
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF が見つかりません: {pdf_path}")
    reader = PdfReader(str(pdf_path))
    pages: list[Document] = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        # metadata: source=ファイル名（可搬性）, page=0始まりのページ番号
        pages.append(Document(page_content=text, metadata={"source": pdf_path.name, "page": i}))
    return pages


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


def load_and_chunk(pdf_path: str | Path) -> list[Document]:
    """PDF 読み込み → チャンク分割 をまとめて実行する便利関数。"""
    return chunk_documents(load_pdf(pdf_path))
