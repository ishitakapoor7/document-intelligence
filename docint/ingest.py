"""Ingest: bytes -> typed, field-extracted, located, status-tagged chunks.

The recognition ladder has two triggers and one floor:

    low confidence  -> vision
    missing fields  -> vision, re-extract
    still missing   -> human review, extraction skipped
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

    # 1. Content hash first: an unchanged file short-circuits every model call below.
    digest = content_hash(path)
    if not force and digest in manifest:
        return IngestOutcome(status="unchanged", detail=manifest[digest].document_id,
                             remembered=manifest[digest])

    # 2. Parse. Locations attached in-loop; IDs from content hash + location.
    try:
        document, chunks = parse(path)
    except UnsupportedFileType as exc:
        return IngestOutcome(status="unsupported_format", detail=str(exc))

    # 3. First rung: a poorly-read scan goes to vision before anything judges it.
    #    `force_vision` overrides the gate, since confidence measures the words OCR
    #    found and is silent about the ones it dropped.
    tess = document.ocr.tesseract_confidence
    if (vision_fallback and tess is not None and path.suffix.lower() == ".pdf"
            and (force_vision or tess < MIN_OCR_FOR_VISION_FALLBACK)):
        document.escalated_on = "low_confidence"
        chunks = _escalate(path, chunks, document)

    # 4. Classify. An unclear document becomes `unknown` rather than forced into a
    #    label; classification survives poor recognition better than extraction does.
    document.document_type, document.classification_confidence = classify(chunks)
    chunks = finalize_chunks(chunks, document)

    # 5. Quality floor, on the effective confidence (i.e. after any fallback). Runs
    #    before the `unknown` short-circuit: "we cannot identify this" is a conclusion
    #    drawn from text, and is not available when the text cannot be read.
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

    # 6b. Second rung: a missing required field is direct evidence that recognition
    #     did not deliver what the schema needs, where a confidence score is only a
    #     proxy. Fires once, only for a scan, only when something is actually absent,
    #     so the cost lands on the documents that failed. The type is not re-derived.
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

        # A better transcription can reveal the page was worse than Tesseract said.
        effective = document.ocr_mean_confidence
        if effective is not None and effective < MIN_OCR_CONFIDENCE:
            document.status = "needs_review_ocr"
            document.review_reason = (
                f"required field(s) {', '.join(document.missing_before_escalation)} were "
                f"missing, so the page was re-read with vision; legibility came back "
                f"{effective:.1f}, below the {MIN_OCR_CONFIDENCE:.0f} threshold.")
            return _finish(manifest, document, chunks, started, index)

    # 7. A required field we could not read is a review item, not a silent gap. The
    #    classification is preserved so a reviewer knows what they are looking at.
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
    """Re-read with vision and record the cost. Both rungs come through here, so the
    telemetry is recorded identically either way."""
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
    # Indexed regardless of status: a document in review is still findable, it just
    # carries no asserted values. Hiding it would make questions about it return
    # nothing rather than a flagged answer.
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
