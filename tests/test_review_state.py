"""The review verdict must survive the run that produced it.

It previously lived only in the terminal output of that ingest, so the second run
reported `unchanged` and said nothing about a document sitting in review. A system
that decides something needs a human and forgets by the next run has escalated
nothing, so the persistence gets a test.
"""
import json
from datetime import datetime, timezone

from docint.models import IngestOutcome, ManifestEntry


def entry(**kw) -> ManifestEntry:
    base = dict(content_hash="a" * 64, document_id="doc_aaaaaaaaaaaa",
                filename="x.pdf", document_type="invoice", chunk_ids=["c1"],
                ingested_at=datetime.now(timezone.utc))
    return ManifestEntry(**{**base, **kw})


def test_review_verdict_survives_a_json_round_trip():
    original = entry(status="needs_review_missing_fields",
                     review_reason="invoice_number could not be read",
                     missing_required_fields=["invoice_number", "invoice_date"],
                     ocr_mean_confidence=90.1)
    restored = ManifestEntry.model_validate(json.loads(original.model_dump_json()))
    assert restored.status == "needs_review_missing_fields"
    assert restored.missing_required_fields == ["invoice_number", "invoice_date"]
    assert restored.ocr_mean_confidence == 90.1
    assert restored.review_reason


def test_a_clean_document_defaults_to_extracted():
    # So an older manifest written before these fields existed still loads.
    assert entry().status == "extracted"
    assert entry().missing_required_fields == []


def test_unchanged_outcome_carries_what_the_manifest_remembered():
    remembered = entry(status="needs_review_ocr", ocr_mean_confidence=51.7)
    outcome = IngestOutcome(status="unchanged", detail=remembered.document_id,
                            remembered=remembered)
    assert outcome.remembered is not None
    assert outcome.remembered.status == "needs_review_ocr"
