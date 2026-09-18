"""Every data model in the system. One file, on purpose.

The whole pipeline is built on four types: a Document (one file), the Chunks it
parses into, the Fields extracted from it, and a ManifestEntry recording that it
was ingested. Everything downstream - retrieval, citation, evaluation - reads
these and nothing else.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FileType = Literal["pdf_digital", "pdf_scanned", "xlsx"]
DocumentType = Literal["invoice", "purchase_order", "vendor_record", "unknown"]


class SourceLocation(BaseModel, frozen=True):
    """Where a chunk came from, precisely enough for a human to go and look.

    Deliberately flat rather than a discriminated union: this doubles as its own
    vector-store metadata, so there is no to_metadata/from_metadata pair to keep
    in sync and no round-trip test to guard. Two shapes do not earn a hierarchy.
    """

    kind: Literal["pdf_page", "xlsx_range"]
    page: int | None = None          # pdf_page
    sheet: str | None = None         # xlsx_range
    cell_range: str | None = None    # xlsx_range, e.g. "A4:B9"

    def render(self) -> str:
        """Human-facing: 'p. 2' | 'Vendors!A4:B9'."""
        if self.kind == "pdf_page":
            return f"p. {self.page}"
        return f"{self.sheet}!{self.cell_range}"

    def canonical(self) -> str:
        """Identity-facing: stable string that feeds the chunk_id hash.

        Distinct from render() because the human form is free to change wording;
        this one must never change, or every chunk_id in every gold file moves.
        """
        if self.kind == "pdf_page":
            return f"page={self.page}"
        return f"sheet={self.sheet}&cells={self.cell_range}"


class Chunk(BaseModel, frozen=True):
    """One retrievable, citable unit of text.

    Document-level facts (type, file type, filename, access tag) are denormalised
    onto every chunk so that a chunk pulled back out of the vector store is
    self-describing: filterable, citable and renderable without a second lookup.
    """

    chunk_id: str
    document_id: str
    document_type: DocumentType
    file_type: FileType
    filename: str
    access_tag: str
    text: str
    source_location: SourceLocation
    ocr_confidence: float | None = None   # None => came from a real text layer

    def render_source(self) -> str:
        return f"{self.filename} - {self.source_location.render()}"


class ExtractedField(BaseModel):
    """A single structured value, and the chunk it was read out of."""

    name: str
    value: str | float | None
    chunk_id: str
    ocr_confidence: float | None = None    # inherited from the chunk it came from

    @property
    def low_confidence(self) -> bool:
        from docint.config import MIN_OCR_CONFIDENCE
        return self.ocr_confidence is not None and self.ocr_confidence < MIN_OCR_CONFIDENCE


class Document(BaseModel):
    """One ingested file."""

    document_id: str                       # doc_<first 12 of content_hash>
    filename: str
    file_type: FileType
    content_hash: str                      # sha256 of the raw bytes
    access_tag: str
    page_count: int = 0

    document_type: DocumentType = "unknown"
    classification_confidence: float = 0.0
    fields: list[ExtractedField] = Field(default_factory=list)

    ocr_mean_confidence: float | None = None   # None for born-digital
    ocr_route: Literal["tesseract", "claude"] | None = None
    needs_review: bool = False
    review_reason: str | None = None


class ManifestEntry(BaseModel):
    """Proof that a given byte-sequence was already ingested.

    Keyed by content hash, so re-ingesting an unchanged file is detected before
    any parsing, OCR or model call happens.
    """

    content_hash: str
    document_id: str
    filename: str
    document_type: DocumentType
    chunk_ids: list[str]
    ingested_at: datetime
