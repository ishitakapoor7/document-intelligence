"""Every data model, in one file.

Four types carry the pipeline: a Document (one file), the Chunks it parses into, the
Fields extracted from it, and a ManifestEntry recording the ingest.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FileType = Literal["pdf_digital", "pdf_scanned", "xlsx"]
DocumentType = Literal["invoice", "purchase_order", "vendor_record", "unknown"]


class SourceLocation(BaseModel, frozen=True):
    """Where a chunk came from, precisely enough for a human to go and look.

    Flat rather than a discriminated union: it doubles as its own vector-store
    metadata, so there is no to_metadata/from_metadata pair to keep in sync.
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
        """Feeds the chunk_id hash, so it must never change - unlike render(), whose
        wording is free to."""
        if self.kind == "pdf_page":
            return f"page={self.page}"
        return f"sheet={self.sheet}&cells={self.cell_range}"


class Chunk(BaseModel, frozen=True):
    """One retrievable, citable unit of text.

    Document-level facts are denormalised onto every chunk so one pulled from the
    store is self-describing: filterable, citable and renderable without a lookup.
    """

    chunk_id: str
    document_id: str
    document_type: DocumentType
    file_type: FileType
    filename: str
    access_tag: str
    text: str
    source_location: SourceLocation
    # How this text was produced, and the MEASURED confidence of reading it.
    # `ocr_confidence` is always Tesseract's per-word score; a vision model has no
    # equivalent, so a vision-read chunk keeps the measured score of the Tesseract
    # pass that triggered it and is distinguished by `recognition`.
    recognition: Literal["text_layer", "tesseract", "vision"] = "text_layer"
    ocr_confidence: float | None = None

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

    `post_fallback_confidence` is the vision model's self-reported legibility and is
    NOT comparable to Tesseract's measured per-word score. Labelled as model-graded
    wherever shown, never mixed into a measured average.
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
    source_id: str = ""                    # stable per FILE, independent of bytes
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

    # Which rung sent this page to vision: the page looked bad, or it read clean and
    # still did not yield what the schema required.
    escalated_on: Literal["low_confidence", "missing_fields"] | None = None
    missing_before_escalation: list[str] = Field(default_factory=list)

    ocr: OcrTelemetry = Field(default_factory=OcrTelemetry)
    extraction_cost_usd: float = 0.0
    total_latency_s: float = 0.0

    @property
    def needs_review(self) -> bool:
        return self.status.startswith("needs_review")

    @property
    def ocr_mean_confidence(self) -> float | None:
        """The MEASURED recognition confidence - Tesseract's, always.

        The vision model's self-rating is deliberately not returned here. It is not a
        measurement, and a model's opinion of its own reading must not be what
        authorizes extraction from that reading. It stays in
        `ocr.post_fallback_confidence`, labelled model-graded wherever it is shown.
        """
        return self.ocr.tesseract_confidence


class ManifestEntry(BaseModel):
    """What is currently indexed for one source file.

    Keyed by source_id, not content hash: the same bytes under two access tags are two
    documents, and a file whose contents change is the same document at a new version.
    The stored content_hash is what makes an unchanged file free to re-ingest.
    """

    source_id: str = ""
    content_hash: str
    document_id: str
    filename: str
    access_tag: str = ""
    document_type: DocumentType
    chunk_ids: list[str]
    ingested_at: datetime

    # The review verdict is persisted, not merely printed, so idempotence does not
    # empty the queue: a re-ingest still reports what is waiting on a human.
    status: DocumentStatus = "extracted"
    review_reason: str | None = None
    missing_required_fields: list[str] = Field(default_factory=list)
    ocr_mean_confidence: float | None = None


class IngestOutcome(BaseModel):
    """What happened to one file. Status mirrors DocumentStatus, so there is one
    vocabulary rather than two that can disagree."""

    status: DocumentStatus
    document: Document | None = None
    chunks: list[Chunk] = Field(default_factory=list)
    detail: str | None = None
    # What the manifest already knew about this file, when status == "unchanged".
    remembered: "ManifestEntry | None" = None

    @property
    def did_work(self) -> bool:
        return self.status != "unchanged"


# --------------------------------------------------------------------------- #
# Query path
# --------------------------------------------------------------------------- #

class Claim(BaseModel):
    """One factual assertion, and the chunks it rests on. `cited_chunk_ids` are real
    chunk IDs, so nothing translates between what was said and what it resolves to."""

    text: str
    cited_chunk_ids: list[str] = Field(min_length=1)


class DraftAnswer(BaseModel):
    answer: str
    claims: list[Claim] = Field(default_factory=list)

    # Whether the chunks answer the QUESTION, distinct from whether any true claim can
    # be made from them. Holding only a contract, the model can state true facts about
    # it while being unable to compare it to an invoice; counting claims calls that an
    # answer.
    answers_question: bool = True


class Verdict(BaseModel):
    """A verifier's judgement on one claim against its cited chunks."""

    supported: bool
    reason: str


class Citation(BaseModel):
    chunk_id: str
    filename: str
    location: str
    document_type: DocumentType
    recognition: Literal["text_layer", "tesseract", "vision"] = "text_layer"
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

    # Set when a claim was planted by fault injection rather than produced by the
    # model, so an eval report never mistakes it for a real hallucination.
    injected_claim: str | None = None


QueryStatus = Literal["answered", "refused", "human_review"]


class QueryTrace(BaseModel):
    """One JSON file per query, and the only output format: `ask` renders it, `eval`
    scores a list of them, `show` resolves its citations. No second account of what
    happened that could disagree."""

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
