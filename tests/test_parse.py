"""Parser contract: locations are real, IDs are stable, OCR confidence is sane."""
from pathlib import Path

import pytest

from docint.config import ROOT
from docint.models import SourceLocation
from docint.parse import UnsupportedFileType, make_chunk_id, parse

INVOICE = ROOT / "corpus" / "invoice_acme_001.pdf"
PO = ROOT / "corpus" / "po_acme_001.pdf"
VENDORS = ROOT / "corpus" / "vendor_records.xlsx"


def test_digital_pdf_uses_text_layer_not_ocr():
    doc, chunks = parse(INVOICE)
    assert doc.file_type == "pdf_digital"
    assert doc.ocr_mean_confidence is None
    assert all(c.ocr_confidence is None for c in chunks)
    assert "INV-2026-0117" in chunks[0].text


def test_scanned_pdf_routes_through_ocr_with_confidence():
    doc, chunks = parse(PO)
    assert doc.file_type == "pdf_scanned"
    assert doc.ocr_route == "tesseract"
    assert 0.0 <= doc.ocr_mean_confidence <= 100.0
    assert all(0.0 <= c.ocr_confidence <= 100.0 for c in chunks)
    assert "PO-2026-0043" in chunks[0].text


def test_locations_are_attached_during_parsing():
    _, pdf_chunks = parse(PO)
    assert pdf_chunks[0].source_location == SourceLocation(kind="pdf_page", page=1)

    _, xlsx_chunks = parse(VENDORS)
    terms = [c for c in xlsx_chunks if c.source_location.cell_range == "A4:B9"]
    assert len(terms) == 1, "the vendor terms block should be one citable chunk"
    assert "Acme Industrial Supply Co." in terms[0].text
    assert terms[0].source_location.sheet == "Vendors"


def test_chunk_ids_are_stable_across_parses():
    first = [c.chunk_id for c in parse(PO)[1]]
    second = [c.chunk_id for c in parse(PO)[1]]
    assert first == second


def test_chunk_id_ignores_text_so_ocr_drift_cannot_move_it():
    """The load-bearing property: identity is content hash + location, never text.

    Tesseract output varies across versions and settings. If chunk_id hashed the
    text, every gold citation would break on exactly the scanned documents where
    stability matters most.
    """
    location = SourceLocation(kind="pdf_page", page=1)
    assert make_chunk_id("abc123", location) == make_chunk_id("abc123", location)
    # different location -> different id
    assert make_chunk_id("abc123", location) != make_chunk_id(
        "abc123", SourceLocation(kind="pdf_page", page=2)
    )
    # different document -> different id
    assert make_chunk_id("abc123", location) != make_chunk_id("def456", location)


def test_access_tags_come_from_config_not_the_file():
    assert parse(VENDORS)[0].access_tag == "general"
    assert parse(PO)[0].access_tag == "procurement"


def test_unsupported_format_is_a_typed_failure():
    with pytest.raises(UnsupportedFileType):
        parse(ROOT / "requirements.txt")
