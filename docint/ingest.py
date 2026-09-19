"""The ingest pipeline: bytes -> typed, field-extracted, located, status-tagged chunks.

Plain Python orchestration. The recognition ladder is the interesting part:

    Tesseract -> below MIN_OCR_FOR_VISION_FALLBACK? -> Claude vision -> re-measure
              -> still below MIN_OCR_CONFIDENCE?    -> human review, extraction skipped

Classification runs BEFORE the quality gate, because it survives poor recognition far
better than field extraction does. A review item that says "this is a purchase order
whose numbers cannot be trusted" is worth more than an untyped blob.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from docint.config import (
    MANIFEST_PATH,
    MIN_OCR_CONFIDENCE,
    MIN_OCR_FOR_VISION_FALLBACK,
    RUNS_DIR,
    required_fields,
)
from docint.models import Chunk, Document, IngestOutcome, ManifestEntry
from docint.parse import UnsupportedFileType, content_hash, finalize_chunks, parse
from docint.understand import classify, extract


def load_manifest() -> dict[str, ManifestEntry]:
    if not MANIFEST_PATH.exists():
        return {}
    raw = json.loads(MANIFEST_PATH.read_text())
    return {k: ManifestEntry.model_validate(v) for k, v in raw.items()}


def save_manifest(manifest: dict[str, ManifestEntry]) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps({k: json.loads(v.model_dump_json()) for k, v in manifest.items()}, indent=2)
    )


def ingest_document(path: Path, manifest: dict[str, ManifestEntry], *,
                    force: bool = False, vision_fallback: bool = True,
                    force_vision: bool = False, index=None) -> IngestOutcome:
    path = Path(path)
    started = time.perf_counter()

    # 1. Content hash first - an unchanged file short-circuits everything below,
    #    including every model call. This is why a re-run is instant and free.
    digest = content_hash(path)
    if not force and digest in manifest:
        return IngestOutcome(status="unchanged", detail=manifest[digest].document_id,
                             remembered=manifest[digest])

    # 2. Parse. Locations attached in-loop; IDs from content hash + location.
    try:
        document, chunks = parse(path)
    except UnsupportedFileType as exc:
        return IngestOutcome(status="unsupported_format", detail=str(exc))

    # 3. Recognition ladder: escalate a poorly-read scan to vision before judging it.
    # `force_vision` exists because Tesseract confidence measures the words it FOUND
    # and says nothing about what it never found. gkdb0226.pdf scores 90.1 - above the
    # escalation gate - while having silently dropped the entire P.O. Number field and
    # misread the letterhead. Escalated anyway, the same page yields the invoice
    # number, the correct vendor and the struck/surviving PO pair. Coverage is
    # invisible to confidence, so the control is exposed rather than the gate retuned
    # on one document.
    tess = document.ocr.tesseract_confidence
    if (vision_fallback and tess is not None and path.suffix.lower() == ".pdf"
            and (force_vision or tess < MIN_OCR_FOR_VISION_FALLBACK)):
        document.escalated_on = "low_confidence"
        chunks = _escalate(path, chunks, document)

    # 4. Classify. Runs before the quality gate; an unclear document becomes `unknown`
    #    rather than being forced into a label.
    document.document_type, document.classification_confidence = classify(chunks)
    chunks = finalize_chunks(chunks, document)

    # 5. Quality gate, applied to the EFFECTIVE confidence - i.e. after any fallback.
    #    Checked BEFORE the `unknown` short-circuit, because "we could not identify
    #    this document" is a conclusion drawn FROM the text, and it is not available
    #    when the text itself is unreadable. lmcj0190.pdf reads at 54.0 even after
    #    vision and was reported `unknown_type` - true, but it sent a reviewer looking
    #    for a missing document type when the actionable fact was that the scan is
    #    barely legible. The classification is still recorded on the document either
    #    way; only the status changes, and it now names the earlier cause.
    effective = document.ocr_mean_confidence
    if effective is not None and effective < MIN_OCR_CONFIDENCE:
        document.status = "needs_review_ocr"
        document.review_reason = (
            f"recognition confidence {effective:.1f} is below the {MIN_OCR_CONFIDENCE:.0f} "
            f"threshold" + (" even after vision fallback" if document.ocr.fallback_used else "")
            + "; extraction skipped rather than asserting values read from text we cannot trust."
            + (f" Classified `{document.document_type}`, which is itself unreliable at this "
               f"confidence." if document.document_type == "unknown" else ""))
        return _finish(manifest, document, chunks, started, index)

    if document.document_type == "unknown":
        document.status = "unknown_type"
        document.review_reason = (
            f"classification confidence {document.classification_confidence:.2f} below "
            f"threshold, or no configured type fits; no schema to extract against")
        return _finish(manifest, document, chunks, started, index)

    # 6. Extract.
    document.fields, document.line_items, document.extraction_cost_usd = extract(document, chunks)
    missing = _missing_required(document)

    # 6b. Second rung: escalate on a FIELD DEFICIT, not just on low confidence.
    #
    #     A missing required field is direct evidence that recognition failed to
    #     deliver what the schema needs. A confidence score is only a proxy for
    #     that, and a poor one: it averages the words Tesseract FOUND and is
    #     completely silent about the ones it dropped. gkdb0226.pdf reads at 90.1 -
    #     comfortably above the confidence gate - having lost the invoice number,
    #     the date and the entire P.O. Number field. Nothing about 90.1 could have
    #     revealed that; "the schema asked for five values and recognition produced
    #     three" says it outright.
    #
    #     So the ladder now has two independent triggers and one floor:
    #
    #        poor confidence  -> vision            (the page looks bad)
    #        missing fields   -> vision, re-extract (the page read clean and still
    #                                                did not yield what we need)
    #        still missing    -> human review
    #
    #     Only fires once, only for a scan, and only when something is actually
    #     absent - so the cost lands exactly on the documents that failed. The
    #     document type is NOT re-derived: classification survives poor recognition
    #     well (3/3 on the holdouts before any escalation), and re-running it would
    #     spend a call to re-answer a question already answered.
    if (missing and vision_fallback and not document.ocr.fallback_used
            and document.file_type == "pdf_scanned" and path.suffix.lower() == ".pdf"):
        document.escalated_on = "missing_fields"
        document.missing_before_escalation = missing
        chunks = _escalate(path, chunks, document)
        chunks = finalize_chunks(chunks, document)
        fields, line_items, cost = extract(document, chunks)
        document.fields, document.line_items = fields, line_items
        document.extraction_cost_usd += cost
        missing = _missing_required(document)

        # The better transcription can also reveal that the page was worse than
        # Tesseract claimed. Re-apply the floor rather than asserting values off it.
        effective = document.ocr_mean_confidence
        if effective is not None and effective < MIN_OCR_CONFIDENCE:
            document.status = "needs_review_ocr"
            document.review_reason = (
                f"required field(s) {', '.join(document.missing_before_escalation)} were "
                f"missing, so the page was re-read with vision; legibility came back "
                f"{effective:.1f}, below the {MIN_OCR_CONFIDENCE:.0f} threshold.")
            return _finish(manifest, document, chunks, started, index)

    # 7. A required field we could not read is a review item, NOT a silent gap - and
    #    the classification is preserved so the reviewer knows what they are looking at.
    if missing:
        document.missing_required_fields = missing
        document.status = "needs_review_missing_fields"
        tried_vision = (" even after re-reading the page with vision"
                        if document.escalated_on == "missing_fields" else "")
        document.review_reason = (
            f"required field(s) {', '.join(missing)} could not be read from this "
            f"{document.document_type.replace('_', ' ')}{tried_vision}; the document is "
            f"typed but incomplete.")
    else:
        document.status = "extracted"

    return _finish(manifest, document, chunks, started, index)


def _missing_required(document: Document) -> list[str]:
    present = {f.name for f in document.fields if f.value is not None}
    return [n for n in required_fields(document.document_type) if n not in present]


def _escalate(path: Path, chunks: list[Chunk], document: Document) -> list[Chunk]:
    """Re-read the page with Claude vision and record what that cost.

    One function because the ladder now reaches it from two rungs - poor confidence
    before classification, and a field deficit after extraction - and the telemetry
    must be recorded identically either way.
    """
    from docint.vision_ocr import escalate

    chunks, legibility, cost, latency = escalate(path, chunks)
    document.ocr.fallback_used = True
    document.ocr.fallback_route = "claude"
    document.ocr.post_fallback_confidence = legibility
    document.ocr.fallback_cost_usd = cost
    document.ocr.fallback_latency_s = latency
    return chunks


def _finish(manifest, document: Document, chunks: list[Chunk], started: float,
            index=None) -> IngestOutcome:
    # Indexed regardless of status: a document routed to review or typed `unknown` is
    # still findable, it simply carries no asserted field values. Hiding it would mean
    # a question about it silently returns nothing rather than a flagged answer.
    if index is not None and chunks:
        from docint.index import upsert
        upsert(index, chunks)
    document.total_latency_s = time.perf_counter() - started
    manifest[document.content_hash] = ManifestEntry(
        content_hash=document.content_hash,
        document_id=document.document_id,
        filename=document.filename,
        document_type=document.document_type,
        chunk_ids=[c.chunk_id for c in chunks],
        ingested_at=datetime.now(timezone.utc),
        status=document.status,
        review_reason=document.review_reason,
        missing_required_fields=document.missing_required_fields,
        ocr_mean_confidence=document.ocr_mean_confidence,
    )
    return IngestOutcome(status=document.status, document=document, chunks=chunks,
                         detail=document.review_reason)


def ingest_directory(directory: Path, *, force: bool = False, vision_fallback: bool = True,
                     force_vision: bool = False,
                     index=None) -> list[tuple[Path, IngestOutcome]]:
    manifest = load_manifest()
    if index is None:
        from docint.index import open_index
        index = open_index()
    results = []
    for path in sorted(Path(directory).rglob("*")):
        if path.is_dir() or path.name.startswith("."):
            continue
        results.append((path, ingest_document(path, manifest, force=force,
                                              vision_fallback=vision_fallback,
                                              force_vision=force_vision, index=index)))
    save_manifest(manifest)
    return results
