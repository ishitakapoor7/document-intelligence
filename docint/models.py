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
        from docint.config import LOW_CONFIDENCE_FIELD
        return self.ocr_confidence is not None and self.ocr_confidence < LOW_CONFIDENCE_FIELD


class LineItem(BaseModel):
    """One row of a repeating table. Scalars describe a document; these describe rows."""

    description: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None
    chunk_id: str | None = None


DocumentStatus = Literal[
    "extracted",                    # typed, and every required field present
    "needs_review_missing_fields",  # typed, but a required field could not be read
    "needs_review_ocr",             # recognition too poor to extract from at all
    "unknown_type",                 # no confident label, so no schema to extract against
    "unsupported_format",
    "unchanged",                    # content hash already in the manifest
]


class OcrTelemetry(BaseModel):
    """What the recognition stage did, and what it cost.

    `post_fallback_confidence` is NOT comparable to `tesseract_confidence`. Tesseract
    reports a per-word confidence derived from its own classifier. A vision model has
    no such signal, so the number here is the model's SELF-REPORTED legibility - a
    model-graded figure, labelled as such wherever it is displayed, and never mixed
    into a measured average.
    """

    tesseract_confidence: float | None = None
    fallback_used: bool = False
    fallback_route: Literal["claude"] | None = None
    post_fallback_confidence: float | None = None   # model-reported, not measured
    fallback_cost_usd: float = 0.0
    fallback_latency_s: float = 0.0


class Document(BaseModel):
    """One ingested file."""

    document_id: str                       # doc_<first 12 of content_hash>
    filename: str
    file_type: FileType
    content_hash: str
    access_tag: str
    page_count: int = 0

    document_type: DocumentType = "unknown"
    classification_confidence: float = 0.0
    fields: list[ExtractedField] = Field(default_factory=list)
    line_items: list[LineItem] = Field(default_factory=list)

    status: DocumentStatus = "extracted"
    missing_required_fields: list[str] = Field(default_factory=list)
    review_reason: str | None = None

    ocr: OcrTelemetry = Field(default_factory=OcrTelemetry)
    extraction_cost_usd: float = 0.0
    total_latency_s: float = 0.0

    @property
    def needs_review(self) -> bool:
        return self.status.startswith("needs_review")

    @property
    def ocr_mean_confidence(self) -> float | None:
        """Effective confidence: the fallback's figure when one was used.

        Deliberately a property rather than a stored field, so there is exactly one
        answer to 'how well was this document read' and it cannot drift from the
        telemetry it derives from.
        """
        if self.ocr.fallback_used and self.ocr.post_fallback_confidence is not None:
            return self.ocr.post_fallback_confidence
        return self.ocr.tesseract_confidence


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


class IngestOutcome(BaseModel):
    """What happened to one file. Status mirrors DocumentStatus so there is one
    vocabulary for 'what happened', not two that can disagree."""

    status: DocumentStatus
    document: Document | None = None
    chunks: list[Chunk] = Field(default_factory=list)
    detail: str | None = None

    @property
    def did_work(self) -> bool:
        return self.status != "unchanged"


# --------------------------------------------------------------------------- #
# Query path
# --------------------------------------------------------------------------- #

class Claim(BaseModel):
    """One factual assertion, and the chunks it rests on.

    `cited_chunk_ids` are REAL chunk IDs, not per-query labels. The model is shown
    the actual IDs and cites them directly, so there is no translation layer between
    what the model said and what the citation resolves to.
    """

    text: str
    cited_chunk_ids: list[str] = Field(min_length=1)


class DraftAnswer(BaseModel):
    answer: str
    claims: list[Claim] = Field(default_factory=list)


class Verdict(BaseModel):
    """A verifier's judgement on one claim against one cited chunk."""

    supported: bool
    reason: str


class Citation(BaseModel):
    chunk_id: str
    filename: str
    location: str
    document_type: DocumentType
    ocr_confidence: float | None = None

    def render(self) -> str:
        return f"{self.filename} - {self.location}"


class StrippedClaim(BaseModel):
    text: str
    cited_chunk_ids: list[str]
    reason: str


class GenerationRound(BaseModel):
    """One pass of generate -> verify -> strip. Two of these at most."""

    round_index: int
    claims: list[Claim] = Field(default_factory=list)
    invalid_citations: list[str] = Field(default_factory=list)
    verdicts: list[tuple[str, Verdict]] = Field(default_factory=list)
    stripped: list[StrippedClaim] = Field(default_factory=list)
    cost_usd: float = 0.0


QueryStatus = Literal["answered", "refused", "human_review"]


class QueryTrace(BaseModel):
    """One JSON file per query - the only output format.

    `ask` renders it, `eval` scores a list of them, `show` resolves citations out of
    it. One model, three consumers: there is no second description of what happened
    that could disagree with this one.
    """

    trace_id: str
    question: str
    principal: str
    access_tags: list[str]
    type_filter: list[str] = Field(default_factory=list)

    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    retrieved_documents: list[str] = Field(default_factory=list)
    rounds: list[GenerationRound] = Field(default_factory=list)
    regeneration_count: int = 0

    final_status: QueryStatus = "refused"
    final_answer: str | None = None
    kept_claims: list[Claim] = Field(default_factory=list)
    final_citations: list[Citation] = Field(default_factory=list)
    refusal_reason: str | None = None
    review_reason: str | None = None

    total_cost_usd: float = 0.0
    total_latency_s: float = 0.0
