"""The ingest pipeline: bytes -> typed, field-extracted, located, indexed chunks.

Plain Python orchestration. The whole flow is nine steps with two early exits,
which a state-machine framework would obscure rather than clarify.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from docint.config import MANIFEST_PATH, MIN_OCR_CONFIDENCE, RUNS_DIR
from docint.models import Chunk, IngestOutcome, ManifestEntry
from docint.parse import UnsupportedFileType, content_hash, finalize_chunks, parse
from docint.understand import classify, extract


# --------------------------------------------------------------------------- #
# Manifest - keyed by content hash, so an unchanged file is detected before any
# parsing, OCR or model call happens.
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #

def ingest_document(path: Path, manifest: dict[str, ManifestEntry],
                    *, force: bool = False) -> IngestOutcome:
    """Ingest one file. Returns what happened and why."""
    path = Path(path)

    # 1. Content hash first: an unchanged file short-circuits everything below,
    #    including both model calls. This is why a re-run is instant and free.
    digest = content_hash(path)
    if not force and digest in manifest:
        return IngestOutcome(status="unchanged", detail=manifest[digest].document_id)

    # 2. Parse - locations attached in-loop, IDs from content hash + location.
    try:
        document, chunks = parse(path)
    except UnsupportedFileType as exc:
        return IngestOutcome(status="unsupported", detail=str(exc))

    # 3. Classify BEFORE the quality gate. Classification survives poor OCR far
    #    better than field extraction does, and "we know this is a purchase order
    #    but cannot trust its numbers" is a far more useful review item than an
    #    untyped blob. It is also the cheap call.
    document.document_type, document.classification_confidence = classify(chunks)
    chunks = finalize_chunks(chunks, document)

    if document.document_type == "unknown":
        _record(manifest, document, chunks)
        return IngestOutcome(
            status="unknown_type", document=document, chunks=chunks,
            detail=(f"classification confidence {document.classification_confidence:.2f} "
                    f"below threshold; no schema to extract against"),
        )

    # 4. Recognition quality gate. Too poor to trust -> human review, NOT silently
    #    extracted. The document is still indexed so it stays findable.
    if (document.ocr_mean_confidence is not None
            and document.ocr_mean_confidence < MIN_OCR_CONFIDENCE):
        document.needs_review = True
        document.review_reason = (
            f"OCR mean confidence {document.ocr_mean_confidence:.1f} is below the "
            f"{MIN_OCR_CONFIDENCE:.0f} threshold; extraction skipped rather than "
            f"asserting values read from text we cannot trust."
        )
        _record(manifest, document, chunks)
        return IngestOutcome(status="needs_review", document=document, chunks=chunks,
                             detail=document.review_reason)

    # 5. Extract against the configured schema for this type.
    document.fields = extract(document, chunks)
    _record(manifest, document, chunks)
    return IngestOutcome(status="ingested", document=document, chunks=chunks)


def _record(manifest: dict[str, ManifestEntry], document, chunks: list[Chunk]) -> None:
    manifest[document.content_hash] = ManifestEntry(
        content_hash=document.content_hash,
        document_id=document.document_id,
        filename=document.filename,
        document_type=document.document_type,
        chunk_ids=[c.chunk_id for c in chunks],
        ingested_at=datetime.now(timezone.utc),
    )


def ingest_directory(directory: Path, *, force: bool = False) -> list[tuple[Path, IngestOutcome]]:
    manifest = load_manifest()
    results = []
    for path in sorted(Path(directory).iterdir()):
        if path.name.startswith(".") or path.is_dir():
            continue
        results.append((path, ingest_document(path, manifest, force=force)))
    save_manifest(manifest)
    return results
