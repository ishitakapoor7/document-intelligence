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
                    index=None) -> IngestOutcome:
    path = Path(path)
    started = time.perf_counter()

    # 1. Content hash first - an unchanged file short-circuits everything below,
    #    including every model call. This is why a re-run is instant and free.
    digest = content_hash(path)
    if not force and digest in manifest:
        return IngestOutcome(status="unchanged", detail=manifest[digest].document_id)

    # 2. Parse. Locations attached in-loop; IDs from content hash + location.
    try:
        document, chunks = parse(path)
    except UnsupportedFileType as exc:
        return IngestOutcome(status="unsupported_format", detail=str(exc))

    # 3. Recognition ladder: escalate a poorly-read scan to vision before judging it.
    tess = document.ocr.tesseract_confidence
    if (vision_fallback and tess is not None and tess < MIN_OCR_FOR_VISION_FALLBACK
            and path.suffix.lower() == ".pdf"):
        from docint.vision_ocr import escalate
        chunks, legibility, cost, latency = escalate(path, chunks)
        document.ocr.fallback_used = True
        document.ocr.fallback_route = "claude"
        document.ocr.post_fallback_confidence = legibility
        document.ocr.fallback_cost_usd = cost
        document.ocr.fallback_latency_s = latency

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

    # 7. A required field we could not read is a review item, NOT a silent gap - and
    #    the classification is preserved so the reviewer knows what they are looking at.
    present = {f.name for f in document.fields if f.value is not None}
    missing = [n for n in required_fields(document.document_type) if n not in present]
    if missing:
        document.missing_required_fields = missing
        document.status = "needs_review_missing_fields"
        document.review_reason = (
            f"required field(s) {', '.join(missing)} could not be read from this "
            f"{document.document_type.replace('_', ' ')}; the document is typed but incomplete.")
    else:
        document.status = "extracted"

    return _finish(manifest, document, chunks, started, index)


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
    )
    return IngestOutcome(status=document.status, document=document, chunks=chunks,
                         detail=document.review_reason)


def ingest_directory(directory: Path, *, force: bool = False, vision_fallback: bool = True,
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
                                              vision_fallback=vision_fallback, index=index)))
    save_manifest(manifest)
    return results
